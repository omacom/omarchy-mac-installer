import Foundation

/// AuraStage1Format's compile-time chip ranges are the ones in
/// scripts/stage1-execute-window.json, the definition the engine
/// (omarchy_chip) and the image builder also follow. Run from Recovery/.
@main struct ExecuteWindowCheck {
  static func main() throws {
    let file = URL(fileURLWithPath: "../scripts/stage1-execute-window.json")
    let document =
      try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as! [String: Any]
    precondition(document["schema_version"] as? Int == 1)
    func chip(_ value: String?) -> UInt64 { UInt64(value!.dropFirst(2), radix: 16)! }
    let ranges = (document["chip_id_ranges"] as! [[String: String]]).map {
      chip($0["first"])...chip($0["last"])
    }
    for id in UInt64(0)...0xFFFF {
      precondition(
        AuraStage1Format.usesExecuteWindow(chipID: id) == ranges.contains { $0.contains(id) },
        "chip 0x\(String(id, radix: 16)) drifts from scripts/stage1-execute-window.json")
    }
    print(
      "PASS the execute-window chips match scripts/stage1-execute-window.json for every chip ID")
  }
}
