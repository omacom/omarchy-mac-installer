#if os(macOS)
  import Foundation
  import TryOmarchyKitClient

  public enum TrialKitAsset: Hashable, Sendable {
    /// `TryOmarchyKit.json` of the latest published release: never a draft or
    /// a prerelease.
    case latestManifest
    case checksum(TryOmarchyKitVersion)
    case archive(TryOmarchyKitVersion)

    public var maximumBytes: Int64 {
      switch self {
      case .latestManifest: Int64(TryOmarchyKitManifest.maximumBytes)
      case .checksum: 1_024
      case .archive: 2 << 30
      }
    }
  }

  public enum TrialKitDownloadError: Error, Equatable, Sendable {
    case httpStatus(Int)
    case tooLarge
    case refusedRedirect
    case failed(String)
  }

  public protocol TrialKitReleaseSource: Sendable {
    /// Downloads the asset to `destination`, which must not exist, and never
    /// more than its `maximumBytes`.
    func fetch(_ asset: TrialKitAsset, to destination: URL) async throws
  }

  /// Try Omarchy's GitHub releases, over Wi-Fi or Ethernet only: like the
  /// installer's other downloads, never on cellular or in Low Data Mode.
  public struct TrialKitReleases: TrialKitReleaseSource {
    public static let defaultBase = URL(string: "https://github.com/omacom/try-omarchy/releases/")!

    public let base: URL

    public init(base: URL = Self.defaultBase) {
      self.base = base
    }

    public func url(for asset: TrialKitAsset) -> URL {
      switch asset {
      case .latestManifest:
        base.appendingPathComponent("latest/download/TryOmarchyKit.json")
      case .checksum(let version):
        base.appendingPathComponent("download/\(version)/TryOmarchyKit.zip.sha256")
      case .archive(let version):
        base.appendingPathComponent("download/\(version)/TryOmarchyKit.zip")
      }
    }

    var allowsLoopbackHTTP: Bool {
      base.scheme == "http" && ["127.0.0.1", "localhost"].contains(base.host)
    }

    public func fetch(_ asset: TrialKitAsset, to destination: URL) async throws {
      let configuration = URLSessionConfiguration.ephemeral
      configuration.allowsExpensiveNetworkAccess = false
      configuration.allowsConstrainedNetworkAccess = false
      configuration.timeoutIntervalForRequest = 60
      configuration.timeoutIntervalForResource = 1_800
      configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
      let url = url(for: asset)
      guard url.scheme == "https" || allowsLoopbackHTTP else {
        throw TrialKitDownloadError.refusedRedirect
      }
      let download = try TrialKitDownload(
        destination: destination, limit: asset.maximumBytes, allowsLoopbackHTTP: allowsLoopbackHTTP)
      try await download.run(url, configuration: configuration)
    }
  }

  /// One download into a new file, refused past its limit and on any
  /// redirect that leaves HTTPS.
  final class TrialKitDownload: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let output: FileHandle
    private let destination: URL
    private let limit: Int64
    private let allowsLoopbackHTTP: Bool
    private let lock = NSLock()
    private var received: Int64 = 0
    private var failure: Error?
    private var continuation: CheckedContinuation<Void, Error>?

    init(destination: URL, limit: Int64, allowsLoopbackHTTP: Bool) throws {
      guard FileManager.default.createFile(atPath: destination.path, contents: nil),
        let output = FileHandle(forWritingAtPath: destination.path)
      else { throw TrialKitDownloadError.failed("could not create the download") }
      self.output = output
      self.destination = destination
      self.limit = limit
      self.allowsLoopbackHTTP = allowsLoopbackHTTP
    }

    func run(_ url: URL, configuration: URLSessionConfiguration) async throws {
      let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
      defer { session.finishTasksAndInvalidate() }
      let task = session.dataTask(with: url)
      do {
        try await withTaskCancellationHandler {
          try await withCheckedThrowingContinuation { continuation in
            lock.withLock { self.continuation = continuation }
            task.resume()
          }
        } onCancel: {
          task.cancel()
        }
        try output.close()
      } catch {
        try? output.close()
        try? FileManager.default.removeItem(at: destination)
        throw error
      }
    }

    private func fail(_ error: Error, _ task: URLSessionTask) {
      lock.withLock { if failure == nil { failure = error } }
      task.cancel()
    }

    func urlSession(
      _ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
      completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
      let status = (response as? HTTPURLResponse)?.statusCode ?? 0
      guard status == 200 else {
        lock.withLock { if failure == nil { failure = TrialKitDownloadError.httpStatus(status) } }
        return completionHandler(.cancel)
      }
      guard response.expectedContentLength <= limit else {
        lock.withLock { if failure == nil { failure = TrialKitDownloadError.tooLarge } }
        return completionHandler(.cancel)
      }
      completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
      let total = lock.withLock { () -> Int64 in
        received += Int64(data.count)
        return received
      }
      guard total <= limit else { return fail(TrialKitDownloadError.tooLarge, dataTask) }
      do {
        try output.write(contentsOf: data)
      } catch {
        fail(TrialKitDownloadError.failed(error.localizedDescription), dataTask)
      }
    }

    func urlSession(
      _ session: URLSession, task: URLSessionTask,
      willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
      completionHandler: @escaping (URLRequest?) -> Void
    ) {
      let scheme = request.url?.scheme
      let host = request.url?.host ?? ""
      let loopback = allowsLoopbackHTTP && ["127.0.0.1", "localhost"].contains(host)
      guard scheme == "https" || (scheme == "http" && loopback) else {
        lock.withLock { failure = TrialKitDownloadError.refusedRedirect }
        return completionHandler(nil)
      }
      completionHandler(request)
    }

    func urlSession(
      _ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?
    ) {
      let (continuation, recorded) = lock.withLock {
        () -> (CheckedContinuation<Void, Error>?, Error?) in
        defer { self.continuation = nil }
        return (self.continuation, failure)
      }
      if let recorded {
        continuation?.resume(throwing: recorded)
      } else if let error {
        continuation?.resume(throwing: TrialKitDownloadError.failed(error.localizedDescription))
      } else if (task.response as? HTTPURLResponse)?.statusCode != 200 {
        continuation?.resume(throwing: TrialKitDownloadError.refusedRedirect)
      } else {
        continuation?.resume()
      }
    }
  }
#endif
