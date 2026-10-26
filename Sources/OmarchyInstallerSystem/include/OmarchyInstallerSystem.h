#ifndef OMARCHY_INSTALLER_SYSTEM_H
#define OMARCHY_INSTALLER_SYSTEM_H

int omarchy_installer_flock(int descriptor, int operation);

/// DKIOCSYNCHRONIZE on a raw disk device: 0 on success, -1 with errno.
int omarchy_installer_synchronize_disk(int descriptor);

#endif
