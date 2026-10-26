#if os(macOS)
  import Foundation
  import TryOmarchyKitClient

  /// What this app asks the run mode to start: always the VM in Try
  /// Omarchy's default folder, whatever folder Try Omarchy chose.
  public enum TrialRunConfiguration {
    /// Whether Omarchy offers Move to Native Install. Only an installer that
    /// stages the export onto the native disk asks for it.
    public static let offersNativeExports = false

    public static func make(
      productName: String, defaultRoot: URL? = TryOmarchyStorage.defaultRoot(),
      nativeExports: Bool = offersNativeExports
    ) throws -> VMRunConfiguration {
      guard let defaultRoot else {
        throw VMRunError(code: .storageUnavailable, message: PlainLanguage.trialNoDefaultFolder)
      }
      return VMRunConfiguration(
        productName: productName, storageRoot: defaultRoot.path, nativeExports: nativeExports)
    }
  }
#endif
