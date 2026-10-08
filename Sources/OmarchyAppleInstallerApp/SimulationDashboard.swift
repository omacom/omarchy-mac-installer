#if DEBUG
  import SwiftUI
  import OmarchyInstallerUXCore
  import OmarchyAppleInstallerTrustCore

  /// Only constructed by the explicit --simulate launch flag. Scenario resets
  /// replace an in-memory environment; the live factory is never called.
  struct SimulationDashboard: View {
    var onSessionAvailable: ((InstallerSession) -> Void)? = nil
    @State private var scenario = Self.launchScenario
    @State private var channel = ReleaseChannel.edge
    @State private var slow = false
    @State private var generation = UUID()
    @State private var environment = InstallerSimulationEnvironment(scenario: Self.launchScenario)
    @State private var canChangeChannel = false
    @State private var session: InstallerSession?

    var body: some View {
      VStack(spacing: 0) {
        VStack(alignment: .leading, spacing: 10) {
          Text("SIMULATION · No installation changes to your Mac")
            .font(OmarchyTheme.heading)
          HStack {
            Picker("Scenario", selection: $scenario) {
              ForEach(InstallerSimulationScenario.allCases) { scenario in
                Text(scenario.title).tag(scenario)
              }
            }
            Button("Reset simulation", action: reset)
          }
          HStack {
            Picker("Test channel", selection: $channel) {
              ForEach(ReleaseChannel.allCases, id: \.self) { option in
                Text(PlainLanguage.badge(for: option)).tag(option)
              }
            }.disabled(!canChangeChannel)
            Toggle("Slow playback", isOn: $slow)
          }
          // The Release channel menu's items, exactly as the app menu words
          // them, so the three channel states can be reviewed in the window.
          Text(
            "Release channel menu: "
              + ReleaseChannel.allCases.map { option in
                let availability = session?.channelAvailability[option]
                let item = PlainLanguage.channelMenuItem(option, availability: availability)
                let enabled = PlainLanguage.channelMenuItemEnabled(
                  option, selected: channel, availability: availability)
                return (option == channel ? "✓ " : "") + item + (enabled ? "" : " (disabled)")
              }.joined(separator: " · ")
          )
          .font(OmarchyTheme.detail)
          .fixedSize(horizontal: false, vertical: true)
          Text(scenario.guidance).font(OmarchyTheme.body).fixedSize(
            horizontal: false, vertical: true)
          Text(
            "Test data only: no downloads, disk changes, or shutdown. Changing the scenario or speed restarts the simulation."
          )
          .font(OmarchyTheme.detail)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .foregroundStyle(OmarchyTheme.text)
        .background(OmarchyTheme.card)
        Divider()
        OnePageInstallerView(
          environment: environment, channel: channel,
          onChannelAvailability: { canChangeChannel = $0 },
          onSessionAvailable: {
            session = $0
            onSessionAvailable?($0)
          }
        )
        .id(generation)
      }
      .task { await continueAtLaunch() }
      .onChange(of: scenario) { _, _ in reset() }
      .onChange(of: slow) { _, _ in reset() }
      .onChange(of: channel) { _, _ in reset() }
    }

    /// `--simulate-scenario=NAME` opens on that scenario, so a screen can be
    /// reviewed or captured without driving the picker. One token: AppKit
    /// would take a separate bare word for a file to open.
    private static var launchScenario: InstallerSimulationScenario {
      let prefix = "--simulate-scenario="
      guard
        let argument = ProcessInfo.processInfo.arguments.first(where: { $0.hasPrefix(prefix) })
      else { return .success }
      return InstallerSimulationScenario(rawValue: String(argument.dropFirst(prefix.count)))
        ?? .success
    }

    /// `--simulate-continue` presses Continue once the launch scenario's
    /// first check is done, to reach its plan or failure screen.
    private func continueAtLaunch() async {
      guard ProcessInfo.processInfo.arguments.contains("--simulate-continue") else { return }
      for _ in 0..<100 {
        if let session, case .welcome = session.phase {
          await session.continueToPlan()
          return
        }
        try? await Task.sleep(for: .milliseconds(100))
      }
    }

    private func reset() {
      environment.cancel()
      environment = InstallerSimulationEnvironment(
        scenario: scenario, channel: channel,
        delay: slow ? .seconds(2) : .milliseconds(400))
      generation = UUID()
      canChangeChannel = false
    }
  }
#endif
