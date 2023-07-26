/* Copyright 2019 The ChromiumOS Authors
 * Use of this source code is governed by a BSD-style license that can be
 * found in the LICENSE file.
 *
 * Functions for initializing the vboot workbuffer and vb2_context.
 */

#include "2api.h"
#include "2common.h"
#include "2misc.h"
#include "2sysincludes.h"

void vb2_workbuf_from_ctx(struct vb2_context *ctx, struct vb2_workbuf *wb)
{
	struct vb2_shared_data *sd = vb2_get_sd(ctx);
	vb2_workbuf_init(wb, (void *)sd + sd->workbuf_used,
			 sd->workbuf_size - sd->workbuf_used);
}

void vb2_set_workbuf_used(struct vb2_context *ctx, uint32_t used)
{
	struct vb2_shared_data *sd = vb2_get_sd(ctx);
	sd->workbuf_used = vb2_wb_round_up(used);
}

vb2_error_t vb2api_init(void *workbuf, uint32_t size,
			struct vb2_context **ctxptr)
{
	struct vb2_shared_data *sd = workbuf;
	*ctxptr = NULL;

	if (!vb2_aligned(workbuf, VB2_WORKBUF_ALIGN))
		return VB2_ERROR_WORKBUF_ALIGN;

	if (size < vb2_wb_round_up(sizeof(*sd)))
		return VB2_ERROR_WORKBUF_SMALL;

	/* Zero out vb2_shared_data (which includes vb2_context). */
	memset(sd, 0, sizeof(*sd));

	/* Initialize shared data. */
	sd->magic = VB2_SHARED_DATA_MAGIC;
	sd->struct_version_major = VB2_SHARED_DATA_VERSION_MAJOR;
	sd->struct_version_minor = VB2_SHARED_DATA_VERSION_MINOR;
	sd->workbuf_size = size;
	sd->workbuf_used = vb2_wb_round_up(sizeof(*sd));

	*ctxptr = &sd->ctx;
	return VB2_SUCCESS;
}

/**
 * Return the current boot mode (normal, recovery, or dev).
 *
 * @param ctx          Vboot context
 * @return Current boot mode (see vb2_boot_mode enum).
 */
static enum vb2_boot_mode get_boot_mode(struct vb2_context *ctx)
{
	if (ctx->flags & VB2_CONTEXT_RECOVERY_MODE)
		return VB2_BOOT_MODE_MANUAL_RECOVERY;

	if (ctx->flags & VB2_CONTEXT_DEVELOPER_MODE)
		return VB2_BOOT_MODE_DEVELOPER;

	return VB2_BOOT_MODE_NORMAL;
}

#pragma GCC diagnostic push
/* Don't warn for the version_minor check even if the checked version is 0. */
#pragma GCC diagnostic ignored "-Wtype-limits"
vb2_error_t vb2api_relocate(void *new_workbuf, const void *cur_workbuf,
			    uint32_t size, struct vb2_context **ctxptr)
{
	const struct vb2_shared_data *cur_sd = cur_workbuf;
	struct vb2_shared_data *new_sd;
	bool update_bootmode = false;

	if (!vb2_aligned(new_workbuf, VB2_WORKBUF_ALIGN))
		return VB2_ERROR_WORKBUF_ALIGN;

	/* Check magic and version. */
	if (cur_sd->magic != VB2_SHARED_DATA_MAGIC)
		return VB2_ERROR_SHARED_DATA_MAGIC;

	if (cur_sd->struct_version_major != VB2_SHARED_DATA_VERSION_MAJOR ||
	    cur_sd->struct_version_minor < VB2_SHARED_DATA_VERSION_MINOR) {
		if (cur_sd->struct_version_major == VB2_SHARED_DATA_VERSION_MAJOR &&
		    cur_sd->struct_version_minor == 0 &&
		    VB2_SHARED_DATA_VERSION_MINOR == 1) {
			/* update the vb2_context struct to add the boot_mode */
			update_bootmode = true;
		} else {
			return VB2_ERROR_SHARED_DATA_VERSION;
		}
	}

	/* Check workbuf integrity. */
	if (cur_sd->workbuf_used < vb2_wb_round_up(sizeof(*cur_sd)))
		return VB2_ERROR_WORKBUF_INVALID;

	if (cur_sd->workbuf_size < cur_sd->workbuf_used)
		return VB2_ERROR_WORKBUF_INVALID;

	if (cur_sd->workbuf_used > size)
		return VB2_ERROR_WORKBUF_SMALL;

	/* Relocate if necessary. */
	if (cur_workbuf != new_workbuf)
		memmove(new_workbuf, cur_workbuf, cur_sd->workbuf_used);

	/* Set the new size, and return the context pointer. */
	new_sd = new_workbuf;
	new_sd->workbuf_size = size;
	*ctxptr = &new_sd->ctx;

	if (update_bootmode) {
		struct vb2_context *ctx = *ctxptr;
		enum vb2_boot_mode *boot_mode = (enum vb2_boot_mode *)&(ctx->boot_mode);
		new_sd->struct_version_minor = 1;
		*boot_mode = get_boot_mode(ctx);
	}

	return VB2_SUCCESS;
}
#pragma GCC diagnostic pop

vb2_error_t vb2api_reinit(void *workbuf, struct vb2_context **ctxptr)
{
	/* Blindly retrieve workbuf_size.  vb2api_relocate() will
	   perform workbuf validation checks. */
	struct vb2_shared_data *sd = workbuf;
	return vb2api_relocate(workbuf, workbuf, sd->workbuf_size, ctxptr);
}
