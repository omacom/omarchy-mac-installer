import Foundation

/// The same rules as Engine/overlay/src/omarchy_reclaim.py. Deletion is refused
/// unless the recovery account already owns the Omarchy volume and the target
/// is the macOS container sitting between iBoot and System Recovery.
struct PartitionSlice: Equatable {
  var name: String
  var uuid: String
  var type: String
  var offset: UInt64
}

enum MacOSContainerRemovalDecision: Equatable {
  case delete(device: String)
  case alreadyAbsent
}

enum MacOSContainerRemovalError: Error, Equatable {
  case refused(String)
}

enum MacOSContainerRemoval {
  static func decide(
    partitions: [PartitionSlice],
    targetUUID: String,
    stubUUID: String,
    iscUUID: String,
    recoveryUUID: String,
    ownerIsVolumeOwner: Bool
  ) throws -> MacOSContainerRemovalDecision {
    guard ownerIsVolumeOwner else {
      throw MacOSContainerRemovalError.refused(
        "the recovery account is not an Omarchy volume owner")
    }
    let identities = [targetUUID, stubUUID, iscUUID, recoveryUUID].map { $0.uppercased() }
    guard identities.allSatisfy({ !$0.isEmpty }), Set(identities).count == 4 else {
      throw MacOSContainerRemovalError.refused("single-boot container identity is incomplete")
    }
    let target = identities[0]
    let isc = identities[2]
    let recovery = identities[3]
    let occupied = partitions.filter { !$0.type.isEmpty }.sorted { $0.offset < $1.offset }
    guard let first = occupied.first, let last = occupied.last,
      first.uuid.uppercased() == isc, first.type == "Apple_APFS_ISC",
      last.uuid.uppercased() == recovery, last.type == "Apple_APFS_Recovery"
    else {
      throw MacOSContainerRemovalError.refused(
        "iBoot and System Recovery are not the ends of the disk")
    }
    guard let match = occupied.first(where: { $0.uuid.uppercased() == target }) else {
      return .alreadyAbsent
    }
    guard match.type == "Apple_APFS", first.offset < match.offset, match.offset < last.offset,
      !match.name.isEmpty
    else {
      throw MacOSContainerRemovalError.refused(
        "the recorded container is not the macOS container between iBoot and System Recovery")
    }
    return .delete(device: match.name)
  }
}
