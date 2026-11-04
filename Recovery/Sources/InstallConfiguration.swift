import Foundation

enum InstallConfigurationError: Error, LocalizedError {
  case invalid
  var errorDescription: String? {
    "This installer is missing valid configuration for its target volume. Run the first installation step again."
  }
}

struct InstallConfiguration: Decodable {
  let schema: Int
  let volumeName: String
  let systemUUID: String
  let groupUUID: String
  let prebootUUID: String
  let espUUID: String
  let bootMode: String
  let stage2Path: String
  let bootstrap: BootstrapAuthorization?
  let allowCustomBootArguments: Bool?
  /// Set only for an experimental single-boot install. Absent means the
  /// recovery account can still be deferred, which is the dual-boot behavior.
  let singleBoot: Bool?
  let macosContainerUUID: String?
  let stubContainerUUID: String?
  let iscContainerUUID: String?
  let recoveryContainerUUID: String?

  static func decode(_ data: Data) throws -> InstallConfiguration {
    let keys: Set<String> = [
      "schema", "volumeName", "systemUUID", "groupUUID", "prebootUUID", "espUUID",
      "bootMode", "stage2Path", "bootstrap",
    ]
    let optionalBools: Set<String> = ["allowCustomBootArguments", "singleBoot"]
    let containerKeys = [
      "macosContainerUUID", "stubContainerUUID", "iscContainerUUID", "recoveryContainerUUID",
    ]
    guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
      Set(object.keys).subtracting(optionalBools).subtracting(containerKeys) == keys
        && optionalBools.allSatisfy({ object[$0] == nil || object[$0] is Bool })
        && containerKeys.allSatisfy({ object[$0] == nil || object[$0] is String })
    else { throw InstallConfigurationError.invalid }
    let config = try JSONDecoder().decode(Self.self, from: data)
    guard config.schema == 2, config.bootMode == "raw", config.stage2Path == "m1n1/boot.bin",
      !config.volumeName.isEmpty, config.volumeName.count <= 80,
      !config.volumeName.contains("/"),
      config.volumeName.unicodeScalars.allSatisfy({ !CharacterSet.controlCharacters.contains($0) })
    else { throw InstallConfigurationError.invalid }
    for identifier in [config.systemUUID, config.groupUUID, config.prebootUUID, config.espUUID] {
      guard let uuid = UUID(uuidString: identifier), uuid.uuidString == identifier,
        identifier != "00000000-0000-0000-0000-000000000000"
      else { throw InstallConfigurationError.invalid }
    }
    let containers = [
      config.macosContainerUUID, config.stubContainerUUID,
      config.iscContainerUUID, config.recoveryContainerUUID,
    ]
    if config.singleBoot == true {
      guard containers.allSatisfy({ value in
        guard let value, let uuid = UUID(uuidString: value), uuid.uuidString == value,
          value != "00000000-0000-0000-0000-000000000000"
        else { return false }
        return true
      }), Set(containers.compactMap { $0 }).count == 4
      else { throw InstallConfigurationError.invalid }
    } else if containers.contains(where: { $0 != nil }) {
      throw InstallConfigurationError.invalid
    }
    if let bootstrap = config.bootstrap {
      guard let values = object["bootstrap"] as? [String: Any],
        Set(values.keys) == ["mode", "sourceSystemUUID", "sourceGroupUUID", "ownerUUIDs"],
        bootstrap.sourceGroupUUID != config.groupUUID,
        bootstrap.sourceSystemUUID != config.systemUUID
      else { throw InstallConfigurationError.invalid }
      try bootstrap.validate()
    }
    return config
  }
}
