#if os(macOS)
  import Foundation
  import Security

  /// A team a fetched kit may be signed by, and the identifier every Mach-O
  /// in the kit carries when that team signs it.
  public struct TrialKitSigner: Equatable, Sendable {
    public let teamIdentifier: String
    public let codeIdentifier: String

    public init(teamIdentifier: String, codeIdentifier: String) {
      self.teamIdentifier = teamIdentifier
      self.codeIdentifier = codeIdentifier
    }

    public static let tryOmarchy = Self(
      teamIdentifier: "RZC79MPD34", codeIdentifier: "dev.tryomarchy.kit")

    public var requirement: String {
      "anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists"
        + " and certificate leaf[field.1.2.840.113635.100.6.1.13] exists"
        + " and certificate leaf[subject.OU] = \"\(teamIdentifier)\""
        + " and identifier \"\(codeIdentifier)\""
    }
  }

  public enum TrialKitSignaturePolicy: Equatable, Sendable {
    case developerID([TrialKitSigner])
    /// Any valid signature, ad hoc included. Only debug builds offer it, for
    /// checking a locally built kit (`TrialKitUpdater.Settings`).
    case anyValidSignature

    /// Fetched kits come only from Try Omarchy's releases. A kit this
    /// installer signs keeps the networking files Try Omarchy signed, so it
    /// is never one team's.
    public static let release = Self.developerID([.tryOmarchy])
  }

  public struct TrialKitCodeCheck: Equatable, Sendable {
    public let satisfiesRequirement: Bool
    public let hasHardenedRuntime: Bool

    public init(satisfiesRequirement: Bool, hasHardenedRuntime: Bool) {
      self.satisfiesRequirement = satisfiesRequirement
      self.hasHardenedRuntime = hasHardenedRuntime
    }
  }

  /// Checks one piece of code: that its signature is valid, and whether it
  /// satisfies a code requirement. A requirement of nil asks for validity
  /// alone. nil means the code is unsigned or its signature is invalid.
  public protocol TrialKitCodeEvaluating: Sendable {
    func check(_ code: URL, requirement: String?) -> TrialKitCodeCheck?
  }

  /// The Security framework's static code checks: every architecture, strict
  /// validation and, for a bundle's executable, the bundle's sealed resources
  /// and nested code.
  public struct SecurityTrialKitCodeEvaluator: TrialKitCodeEvaluating {
    /// Asks whether the signing certificates were revoked, which needs the
    /// network: the check after a download does, the check at each start
    /// follows the system's policy, so Try still starts offline.
    public let enforcesRevocation: Bool

    public init(enforcesRevocation: Bool = false) {
      self.enforcesRevocation = enforcesRevocation
    }

    public func check(_ code: URL, requirement: String?) -> TrialKitCodeCheck? {
      var staticCode: SecStaticCode?
      guard SecStaticCodeCreateWithPath(code as CFURL, [], &staticCode) == errSecSuccess,
        let staticCode
      else { return nil }
      let flags = SecCSFlags(
        rawValue: kSecCSCheckAllArchitectures | kSecCSStrictValidate | kSecCSCheckNestedCode
          | (enforcesRevocation ? SecCSFlags.enforceRevocationChecks.rawValue : 0))
      guard SecStaticCodeCheckValidity(staticCode, flags, nil) == errSecSuccess else { return nil }
      var satisfies = true
      if let requirement {
        var compiled: SecRequirement?
        guard
          SecRequirementCreateWithString(requirement as CFString, [], &compiled) == errSecSuccess,
          let compiled
        else { return nil }
        satisfies = SecStaticCodeCheckValidity(staticCode, flags, compiled) == errSecSuccess
      }
      var information: CFDictionary?
      guard
        SecCodeCopySigningInformation(
          staticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &information)
          == errSecSuccess,
        let values = information as? [String: Any]
      else { return nil }
      let codeFlags = (values[kSecCodeInfoFlags as String] as? NSNumber)?.uint32Value ?? 0
      return TrialKitCodeCheck(
        satisfiesRequirement: satisfies,
        hasHardenedRuntime: codeFlags & SecCodeSignatureFlags.runtime.rawValue != 0)
    }
  }

  public enum TrialKitSignatureRefusal: Error, Equatable, Sendable {
    case noCode
    case invalid(String)
    case untrusted(String)
    case mixedTeams
    case noHardenedRuntime(String)
  }

  /// Checks the signature of every Mach-O under a kit's folder, whatever its
  /// layout: a nested bundle, whose executable stands for the bundle, or
  /// plain helpers. One team signs the whole kit.
  public struct TrialKitSignatureCheck: Sendable {
    public let policy: TrialKitSignaturePolicy
    public let evaluator: any TrialKitCodeEvaluating

    public init(
      policy: TrialKitSignaturePolicy,
      evaluator: any TrialKitCodeEvaluating = SecurityTrialKitCodeEvaluator()
    ) {
      self.policy = policy
      self.evaluator = evaluator
    }

    public func verify(_ root: URL) throws {
      let code = try Self.machOFiles(under: root)
      guard !code.isEmpty else { throw TrialKitSignatureRefusal.noCode }
      var team: TrialKitSigner?
      for file in code {
        let shown = String(file.path.dropFirst(root.path.count + 1))
        switch policy {
        case .anyValidSignature:
          guard evaluator.check(file, requirement: nil) != nil else {
            throw TrialKitSignatureRefusal.invalid(shown)
          }
        case .developerID(let signers):
          let candidates = team.map { [$0] } ?? signers
          var matched: (TrialKitSigner, TrialKitCodeCheck)?
          for signer in candidates {
            guard let result = evaluator.check(file, requirement: signer.requirement) else {
              throw TrialKitSignatureRefusal.invalid(shown)
            }
            if result.satisfiesRequirement {
              matched = (signer, result)
              break
            }
          }
          guard let (signer, result) = matched else {
            throw team == nil
              ? TrialKitSignatureRefusal.untrusted(shown) : TrialKitSignatureRefusal.mixedTeams
          }
          guard result.hasHardenedRuntime else {
            throw TrialKitSignatureRefusal.noHardenedRuntime(shown)
          }
          team = signer
        }
      }
    }

    private static let machOMagics: Set<[UInt8]> = [
      [0xCF, 0xFA, 0xED, 0xFE], [0xCE, 0xFA, 0xED, 0xFE], [0xCA, 0xFE, 0xBA, 0xBE],
      [0xBE, 0xBA, 0xFE, 0xCA],
    ]

    /// The plain files under `root` that start as a Mach-O does, without
    /// following links, in a stable order.
    static func machOFiles(under root: URL) throws -> [URL] {
      var found: [URL] = []
      var pending = [root.path]
      var information = stat()
      while let folder = pending.popLast() {
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: folder) else {
          throw TrialKitSignatureRefusal.noCode
        }
        for name in names.sorted() {
          let path = folder + "/" + name
          guard lstat(path, &information) == 0 else { throw TrialKitSignatureRefusal.noCode }
          switch information.st_mode & S_IFMT {
          case S_IFDIR:
            pending.append(path)
          case S_IFREG:
            guard let handle = FileHandle(forReadingAtPath: path) else { continue }
            let magic = (try? handle.read(upToCount: 4)).map { [UInt8]($0) }
            try? handle.close()
            if let magic, machOMagics.contains(magic) {
              found.append(URL(fileURLWithPath: path, isDirectory: false))
            }
          default:
            continue
          }
        }
      }
      return found.sorted { $0.path < $1.path }
    }
  }
#endif
