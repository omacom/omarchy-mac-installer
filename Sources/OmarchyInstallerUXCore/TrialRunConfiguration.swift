#if os(macOS)
  import Foundation
  import TryOmarchyKitClient

  /// What this app asks the run mode to start: always the VM in Try
  /// Omarchy's default folder, whatever folder Try Omarchy chose.
  public enum TrialRunConfiguration {
    public static func make(
      productName: String, defaultRoot: URL? = TryOmarchyStorage.defaultRoot()
    ) throws -> VMRunConfiguration {
      guard let defaultRoot else {
        throw VMRunError(code: .storageUnavailable, message: PlainLanguage.trialNoDefaultFolder)
      }
      return VMRunConfiguration(productName: productName, storageRoot: defaultRoot.path)
    }
  }
#endif
