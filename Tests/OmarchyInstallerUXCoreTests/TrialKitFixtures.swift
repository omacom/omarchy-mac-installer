#if os(macOS)
  import Foundation
  import Network
  import OmarchyAppleInstallerTrustCore
  import TryOmarchyKitClient
  import XCTest
  import zlib
  @testable import OmarchyInstallerUXCore

  /// A kit's folder as a release unpacks it, with stand-in Mach-O files, and
  /// the assets a release publishes for it.
  struct TrialKitFixture {
    static let machO = Data([0xCF, 0xFA, 0xED, 0xFE, 0x0C, 0x00, 0x00, 0x01])

    let root: URL

    var layout: TrialKitLayout { TrialKitLayout(root: root) }

    static func manifest(_ version: String, protocol runProtocol: Int = 1) -> Data {
      Data(
        """
        {"format":1,"version":"\(version)","runProtocolVersion":\(runProtocol),"buildDate":"2026-10-23T09:30:05Z"}

        """.utf8)
    }

    @discardableResult
    static func make(
      at root: URL, version: String, protocol runProtocol: Int = 1,
      manifest: Data? = nil
    ) throws -> Self {
      let fixture = Self(root: root)
      let layout = fixture.layout
      let files = FileManager.default
      try files.createDirectory(
        at: layout.launcherURL.deletingLastPathComponent(), withIntermediateDirectories: true)
      try files.createDirectory(
        at: layout.manifestURL.deletingLastPathComponent(), withIntermediateDirectories: true)
      try machO.write(to: layout.launcherURL)
      try files.setAttributes([.posixPermissions: 0o755], ofItemAtPath: layout.launcherURL.path)
      try (manifest ?? Self.manifest(version, protocol: runProtocol)).write(to: layout.manifestURL)
      try Data("#!/bin/sh\n".utf8).write(
        to: layout.manifestURL.deletingLastPathComponent().appendingPathComponent("run.sh"))
      return fixture
    }

    /// `TryOmarchyKit.zip` of the folder, as `ditto` makes it.
    func archive(to zip: URL) throws {
      try TrialKitFixture.run("/usr/bin/ditto", ["-c", "-k", root.path, zip.path])
    }

    static func checksum(of zip: URL) throws -> Data {
      Data("\(try TrialKitUpdater.digest(ofFile: zip))  TryOmarchyKit.zip\n".utf8)
    }

    static func run(_ tool: String, _ arguments: [String]) throws {
      let process = Process()
      process.executableURL = URL(fileURLWithPath: tool)
      process.arguments = arguments
      process.standardOutput = FileHandle.nullDevice
      process.standardError = FileHandle.nullDevice
      try process.run()
      process.waitUntilExit()
      guard process.terminationStatus == 0 else {
        throw NSError(domain: tool, code: Int(process.terminationStatus))
      }
    }
  }

  /// Release assets served from memory, recording what was asked for.
  final class FakeTrialKitReleases: TrialKitReleaseSource, @unchecked Sendable {
    private let lock = NSLock()
    private var files: [TrialKitAsset: Data] = [:]
    private var failures: [TrialKitAsset: Error] = [:]
    private var asked: [TrialKitAsset] = []

    var fetched: [TrialKitAsset] { lock.withLock { asked } }

    func serve(_ data: Data, as asset: TrialKitAsset) { lock.withLock { files[asset] = data } }

    func fail(_ asset: TrialKitAsset, with error: Error) {
      lock.withLock { failures[asset] = error }
    }

    /// A release: its manifest, and its kit's archive and digest.
    func publish(_ version: String, protocol runProtocol: Int = 1, under base: URL) throws {
      let kit = base.appendingPathComponent("release-\(version)-\(UUID().uuidString)")
      try TrialKitFixture.make(at: kit, version: version, protocol: runProtocol)
      let zip = base.appendingPathComponent("\(UUID().uuidString).zip")
      try TrialKitFixture(root: kit).archive(to: zip)
      let parsed = try XCTUnwrap(TryOmarchyKitVersion(version))
      serve(TrialKitFixture.manifest(version, protocol: runProtocol), as: .latestManifest)
      serve(try TrialKitFixture.checksum(of: zip), as: .checksum(parsed))
      serve(try Data(contentsOf: zip), as: .archive(parsed))
    }

    func fetch(_ asset: TrialKitAsset, to destination: URL) async throws {
      let (data, failure) = lock.withLock { () -> (Data?, Error?) in
        asked.append(asset)
        return (files[asset], failures[asset])
      }
      if let failure { throw failure }
      guard let data else { throw TrialKitDownloadError.httpStatus(404) }
      guard Int64(data.count) <= asset.maximumBytes else { throw TrialKitDownloadError.tooLarge }
      try data.write(to: destination, options: .withoutOverwriting)
    }
  }

  struct FakeNetwork: InstallerNetworkPathObserving {
    var snapshot = InstallerNetworkPathSnapshot(
      isSatisfied: true, isExpensive: false, isConstrained: false)

    func current() -> InstallerNetworkPathSnapshot { snapshot }

    func updates() -> AsyncStream<InstallerNetworkPathSnapshot> {
      let snapshot = snapshot
      return AsyncStream { continuation in
        continuation.yield(snapshot)
        continuation.finish()
      }
    }
  }

  /// Signatures by file name: each file's team, whether it has the hardened
  /// runtime, and whether its signature is valid at all.
  struct FakeCodeEvaluator: TrialKitCodeEvaluating {
    struct Code: Sendable {
      var team: String? = TrialKitSigner.tryOmarchy.teamIdentifier
      var identifier = TrialKitSigner.tryOmarchy.codeIdentifier
      var runtime = true
      var valid = true
    }

    var code: [String: Code] = [:]
    var fallback = Code()

    func check(_ url: URL, requirement: String?) -> TrialKitCodeCheck? {
      let code = code[url.lastPathComponent] ?? fallback
      guard code.valid else { return nil }
      let satisfies =
        requirement.map { requirement in
          guard let team = code.team else { return false }
          return requirement.contains("subject.OU] = \"\(team)\"")
            && requirement.contains("identifier \"\(code.identifier)\"")
        } ?? true
      return TrialKitCodeCheck(satisfiesRequirement: satisfies, hasHardenedRuntime: code.runtime)
    }
  }

  /// A ZIP archive written by hand, for entries no archiver would make: a
  /// local header that disagrees with the central directory, an entry the
  /// directory leaves out, or a size that lies.
  enum HandMadeZip {
    static let top = TrialKitLayout.archiveFolder

    struct Entry {
      var name: String
      var mode: UInt32 = 0o100644
      var data = Data()
      var flags: UInt16 = 0
      var host: UInt16 = 3
      var deflate = false
      var declaredBytes: UInt32?
      var localName: String?
      var listed = true
      var crc: UInt32?
    }

    static func data(_ entries: [Entry]) -> Data {
      var body = Data()
      var directory = Data()
      var listed = 0
      for entry in entries {
        let name = Data(entry.name.utf8)
        let localName = Data((entry.localName ?? entry.name).utf8)
        let packed =
          entry.deflate
          ? (try! (entry.data as NSData).compressed(using: .zlib)) as Data : entry.data
        let crc =
          entry.crc
          ?? entry.data.withUnsafeBytes {
            UInt32(crc32(0, $0.bindMemory(to: Bytef.self).baseAddress, uInt($0.count)))
          }
        let unpacked = entry.declaredBytes ?? UInt32(entry.data.count)
        let method: UInt16 = entry.deflate ? 8 : 0
        let offset = UInt32(body.count)
        body.append(u32(0x0403_4b50))
        body.append(u16(20) + u16(entry.flags) + u16(method) + u16(0) + u16(0))
        body.append(u32(crc) + u32(UInt32(packed.count)) + u32(unpacked))
        body.append(u16(UInt16(localName.count)) + u16(0))
        body.append(localName + packed)
        guard entry.listed else { continue }
        listed += 1
        directory.append(u32(0x0201_4b50))
        directory.append(u16(entry.host << 8 | 20) + u16(20) + u16(entry.flags) + u16(method))
        directory.append(u16(0) + u16(0))
        directory.append(u32(crc) + u32(UInt32(packed.count)) + u32(unpacked))
        directory.append(u16(UInt16(name.count)) + u16(0) + u16(0) + u16(0) + u16(0))
        directory.append(u32(entry.mode << 16) + u32(offset))
        directory.append(name)
      }
      var end = Data()
      end.append(u32(0x0605_4b50) + u16(0) + u16(0))
      end.append(u16(UInt16(listed)) + u16(UInt16(listed)))
      end.append(u32(UInt32(directory.count)) + u32(UInt32(body.count)) + u16(0))
      return body + directory + end
    }

    private static func u16(_ value: UInt16) -> Data {
      withUnsafeBytes(of: value.littleEndian) { Data($0) }
    }
    private static func u32(_ value: UInt32) -> Data {
      withUnsafeBytes(of: value.littleEndian) { Data($0) }
    }
  }

  /// An HTTP server on the loopback interface that serves files by path.
  final class LoopbackServer: @unchecked Sendable {
    private let listener: NWListener
    private let files: [String: Data]
    private let redirects: [String: String]
    private let queue = DispatchQueue(label: "trial-kit-tests.server")

    init(files: [String: Data], redirects: [String: String] = [:]) throws {
      self.files = files
      self.redirects = redirects
      let parameters = NWParameters.tcp
      parameters.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: .any)
      listener = try NWListener(using: parameters)
    }

    func start() async throws -> URL {
      let ready = AsyncStream<UInt16?> { continuation in
        listener.stateUpdateHandler = { [listener] state in
          switch state {
          case .ready: continuation.yield(listener.port?.rawValue)
          case .failed, .cancelled: continuation.yield(nil)
          default: break
          }
        }
      }
      listener.newConnectionHandler = { [weak self] connection in self?.serve(connection) }
      listener.start(queue: queue)
      for await port in ready {
        guard let port else { break }
        return try XCTUnwrap(URL(string: "http://127.0.0.1:\(port)/"))
      }
      throw URLError(.cannotConnectToHost)
    }

    func stop() { listener.cancel() }

    private func serve(_ connection: NWConnection, received: Data = Data()) {
      if received.isEmpty { connection.start(queue: queue) }
      connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) {
        [weak self] data, _, done, _ in
        guard let self else { return connection.cancel() }
        let request = received + (data ?? Data())
        guard let end = request.range(of: Data("\r\n\r\n".utf8)) else {
          return done ? connection.cancel() : self.serve(connection, received: request)
        }
        let line =
          String(decoding: request[..<end.lowerBound], as: UTF8.self)
          .split(separator: "\r\n").first ?? ""
        let path = line.split(separator: " ").dropFirst().first.map(String.init) ?? ""
        let body = self.files[path]
        let status =
          self.redirects[path].map { "302 Found\r\nLocation: \($0)" }
          ?? (body == nil ? "404 Not Found" : "200 OK")
        var response = Data(
          "HTTP/1.1 \(status)\r\nContent-Length: \(body?.count ?? 0)\r\nConnection: close\r\n\r\n"
            .utf8)
        response.append(body ?? Data())
        connection.send(
          content: response, completion: .contentProcessed { _ in connection.cancel() })
      }
    }
  }
#endif
