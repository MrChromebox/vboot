#!/usr/bin/env python3
# Copyright 2022 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Sign the UEFI binaries in the target directory.

The target directory can be either the root of ESP or /boot of root filesystem.
"""

import argparse
import dataclasses
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional


def ensure_executable_available(name):
    """Exit non-zero if the given executable isn't in $PATH.

    Args:
        name: An executable's file name.
    """
    if not shutil.which(name):
        sys.exit(f"Cannot sign UEFI binaries ({name} not found)")


def ensure_file_exists(path, message):
    """Exit non-zero if the given file doesn't exist.

    Args:
        path: Path to a file.
        message: Error message that will be printed if the file doesn't exist.
    """
    if not path.is_file():
        sys.exit(f"{message}: {path}")


@dataclasses.dataclass(frozen=True)
class Keys:
    """Public and private keys paths.

    Attributes:
        private_key: Path of the private signing key
        sign_cert: Path of the signing certificate
        verify_cert: Path of the verification certificate
        kernel_subkey_vbpubk: Path of the kernel subkey public key
    """

    private_key: os.PathLike
    sign_cert: os.PathLike
    verify_cert: os.PathLike
    kernel_subkey_vbpubk: os.PathLike

    def is_private_key_pkcs11(self) -> bool:
        """Check if the private key is a PKCS#11 URI.

        If the private key starts with "pkcs11:", it should be treated
        as a PKCS#11 URI instead of a local file path.
        """
        return str(self.private_key).startswith("pkcs11:")


class Signer:
    """EFI file signer.

    Attributes:
        temp_dir: Path of a temporary directory used as a workspace.
        keys: An instance of Keys.
    """

    def __init__(self, temp_dir: os.PathLike, keys: Keys):
        self.temp_dir = temp_dir
        self.keys = keys

    def sign_efi_file(self, target):
        """Sign an EFI binary file, if possible.

        Args:
            target: Path of the file to sign.
        """
        logging.info("signing efi file %s", target)

        # Remove any existing signatures, in case the file being signed
        # was signed previously. Allow this to fail, as there may not be
        # any signatures.
        subprocess.run(["sudo", "sbattach", "--remove", target], check=False)

        signed_file = self.temp_dir / target.name
        sign_cmd = [
            "sbsign",
            "--key",
            self.keys.private_key,
            "--cert",
            self.keys.sign_cert,
            "--output",
            signed_file,
            target,
        ]
        if self.keys.is_private_key_pkcs11():
            sign_cmd += ["--engine", "pkcs11"]

        try:
            logging.info("running sbsign: %r", sign_cmd)
            subprocess.run(sign_cmd, check=True)
        except subprocess.CalledProcessError:
            logging.warning("cannot sign %s", target)
            return

        subprocess.run(
            ["sudo", "cp", "--force", signed_file, target], check=True
        )
        try:
            subprocess.run(
                ["sbverify", "--cert", self.keys.verify_cert, target],
                check=True,
            )
        except subprocess.CalledProcessError:
            sys.exit("Verification failed")


def inject_vbpubk(efi_file: os.PathLike, keys: Keys):
    """Update a UEFI executable's vbpubk section.

    The crdyboot bootloader contains an embedded public key in the
    ".vbpubk" section. This function replaces the data in the existing
    section (normally containing a dev key) with the real key.

    Args:
        efi_file: Path of a UEFI file.
        keys: An instance of Keys.
    """
    section_name = ".vbpubk"
    logging.info("updating section %s in %s", section_name, efi_file.name)
    subprocess.run(
        [
            "sudo",
            "objcopy",
            "--update-section",
            f"{section_name}={keys.kernel_subkey_vbpubk}",
            efi_file,
        ],
        check=True,
    )


def sign_target_dir(target_dir: os.PathLike, keys: Keys, efi_glob: str):
    """Sign various EFI files under |target_dir|.

    Args:
        target_dir: Path of a boot directory. This can be either the
            root of the ESP or /boot of the root filesystem.
        keys: An instance of Keys.
        efi_glob: Glob pattern of EFI files to sign, e.g. "*.efi".
    """
    bootloader_dir = target_dir / "efi/boot"
    syslinux_dir = target_dir / "syslinux"
    kernel_dir = target_dir

    # Check for the existence of the key files.
    ensure_file_exists(keys.verify_cert, "No verification cert")
    ensure_file_exists(keys.sign_cert, "No signing cert")
    ensure_file_exists(keys.kernel_subkey_vbpubk, "No kernel subkey public key")
    # Only check the private key if it's a local path rather than a
    # PKCS#11 URI.
    if not keys.is_private_key_pkcs11():
        ensure_file_exists(keys.private_key, "No signing key")

    with tempfile.TemporaryDirectory() as working_dir:
        working_dir = Path(working_dir)
        signer = Signer(working_dir, keys)

        for efi_file in sorted(bootloader_dir.glob(efi_glob)):
            if efi_file.is_file():
                signer.sign_efi_file(efi_file)

        for efi_file in sorted(bootloader_dir.glob("crdyboot*.efi")):
            if efi_file.is_file():
                inject_vbpubk(efi_file, keys)
                signer.sign_efi_file(efi_file)

        for syslinux_kernel_file in sorted(syslinux_dir.glob("vmlinuz.?")):
            if syslinux_kernel_file.is_file():
                signer.sign_efi_file(syslinux_kernel_file)

        kernel_file = (kernel_dir / "vmlinuz").resolve()
        if kernel_file.is_file():
            signer.sign_efi_file(kernel_file)


def get_parser() -> argparse.ArgumentParser:
    """Get CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-dir",
        type=Path,
        help="Path of a boot directory, either the root of the ESP or "
        "/boot of the root filesystem",
        required=True,
    )
    parser.add_argument(
        "--private-key",
        type=Path,
        help="Path of the private signing key",
        required=True,
    )
    parser.add_argument(
        "--sign-cert",
        type=Path,
        help="Path of the signing certificate",
        required=True,
    )
    parser.add_argument(
        "--verify-cert",
        type=Path,
        help="Path of the verification certificate",
        required=True,
    )
    parser.add_argument(
        "--kernel-subkey-vbpubk",
        type=Path,
        help="Path of the kernel subkey public key",
        required=True,
    )
    parser.add_argument(
        "--efi-glob",
        help="Glob pattern of EFI files to sign, e.g. '*.efi'",
        required=True,
    )
    return parser


def main(argv: Optional[List[str]] = None) -> Optional[int]:
    """Sign UEFI binaries.

    Args:
        argv: Command-line arguments.
    """
    logging.basicConfig(level=logging.INFO)

    parser = get_parser()
    opts = parser.parse_args(argv)

    for tool in (
        "objcopy",
        "sbattach",
        "sbsign",
        "sbverify",
    ):
        ensure_executable_available(tool)

    keys = Keys(
        private_key=opts.private_key,
        sign_cert=opts.sign_cert,
        verify_cert=opts.verify_cert,
        kernel_subkey_vbpubk=opts.kernel_subkey_vbpubk,
    )

    sign_target_dir(opts.target_dir, keys, opts.efi_glob)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
