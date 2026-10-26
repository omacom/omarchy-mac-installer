#include "OmarchyInstallerSystem.h"

#include <sys/disk.h>
#include <sys/file.h>
#include <sys/ioctl.h>

int omarchy_installer_flock(int descriptor, int operation) {
  return flock(descriptor, operation);
}

int omarchy_installer_synchronize_disk(int descriptor) {
#ifdef DKIOCSYNCHRONIZE
  dk_synchronize_t request = {0};
  return ioctl(descriptor, DKIOCSYNCHRONIZE, &request);
#else
  (void)descriptor;
  return -1;
#endif
}
