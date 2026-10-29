import AppKit

/// The complete Recovery interface uses only AppKit. The model owns installation
/// work; this controller renders its state and keeps credentials in native fields.
final class InstallerView: NSViewController, NSTextFieldDelegate, NSMenuItemValidation {
  private let model: InstallerModel
  private let log = InstallerLog.shared
  private let titleLabel = NSTextField(labelWithString: "")
  private var titleWidthConstraint: NSLayoutConstraint?
  private var titleTopConstraint: NSLayoutConstraint?
  private let content = NSView()
  private let footer = NSView()
  private var renderedStage: Stage?
  private var renderedAuthorization: Bool?
  private var primaryButton: NSButton?
  private var debugButton: NSButton?
  private var proxyButton: NSButton?
  private var progressIndicator: NSProgressIndicator?
  private var operationLabel: NSTextField?
  private var accountUsernameField: NSTextField?
  private var accountPasswordField: NSSecureTextField?
  private var accountConfirmationField: NSSecureTextField?
  private var accountButton: NSButton?
  private var skipButton: NSButton?
  private var issueShowing = false
  private var issueRepair = false
  private let sheets = SheetPresenter()
  private var authorizationMessage: NSTextField?
  private var authorizationField: NSSecureTextField?
  private var cancelRepairButton: NSButton?
  private var detailsViewer: LogTextView?
  private var copyButton: NSButton?
  private var copyingLog = false
  /// Modifiers held in the key window; they pick the finished screen's action.
  private var heldModifiers: NSEvent.ModifierFlags = []
  private var modifierMonitor: Any?

  init(model: InstallerModel) {
    self.model = model
    super.init(nibName: nil, bundle: nil)
    model.onChange = { [weak self] in self?.refresh() }
    log.onChange = { [weak self] in self?.refresh() }
    sheets.onDismiss = { [weak self] in
      self?.detailsViewer = nil
      self?.copyButton = nil
      self?.refresh()
    }
  }

  required init?(coder: NSCoder) { fatalError("Use the programmatic initializer") }

  deinit {
    model.onChange = nil
    log.onChange = nil
    if let modifierMonitor { NSEvent.removeMonitor(modifierMonitor) }
  }

  override func loadView() {
    let root = NSView(frame: RecoveryWindowLayout.initialFrame)
    root.wantsLayer = true
    root.layer?.backgroundColor = OmarchyTheme.darkCGColor(OmarchyTheme.window)
    root.appearance = NSAppearance(named: .darkAqua)
    view = root

    let wordmark = OmarchyWordmark(frame: .zero)
    wordmark.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(wordmark)

    titleLabel.font = OmarchyTheme.title
    titleLabel.textColor = OmarchyTheme.text
    titleLabel.alignment = .center
    titleLabel.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(titleLabel)

    content.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(content)

    footer.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(footer)

    let titleWidth = titleLabel.widthAnchor.constraint(equalToConstant: 600)
    titleWidthConstraint = titleWidth
    let titleTop = titleLabel.topAnchor.constraint(equalTo: wordmark.bottomAnchor, constant: 28)
    titleTopConstraint = titleTop
    NSLayoutConstraint.activate([
      wordmark.topAnchor.constraint(equalTo: root.topAnchor, constant: 62),
      wordmark.centerXAnchor.constraint(equalTo: root.centerXAnchor),
      wordmark.widthAnchor.constraint(equalToConstant: 400),
      wordmark.heightAnchor.constraint(equalToConstant: 94),
      titleTop,
      titleLabel.centerXAnchor.constraint(equalTo: root.centerXAnchor),
      titleWidth,
      content.topAnchor.constraint(equalTo: titleLabel.bottomAnchor, constant: 24),
      content.centerXAnchor.constraint(equalTo: root.centerXAnchor),
      content.widthAnchor.constraint(equalToConstant: 510),
      content.bottomAnchor.constraint(equalTo: footer.topAnchor, constant: -8),
      footer.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 32),
      footer.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -32),
      footer.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -14),
      footer.heightAnchor.constraint(equalToConstant: 80),
    ])
    refresh()
  }

  override func viewDidAppear() {
    super.viewDidAppear()
    guard modifierMonitor == nil, let window = view.window else { return }
    modifierMonitor = NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) {
      [weak self] event in
      self?.track(event.modifierFlags)
      return event
    }
    let center = NotificationCenter.default
    center.addObserver(
      self, selector: #selector(windowBecameKey), name: NSWindow.didBecomeKeyNotification,
      object: window)
    center.addObserver(
      self, selector: #selector(windowResignedKey), name: NSWindow.didResignKeyNotification,
      object: window)
    track(NSEvent.modifierFlags)
    fitWindow(animate: false)
  }

  /// Only the recovery account form uses the tall window. Resizing keeps the
  /// top edge, so the wordmark and title stay where they were.
  private func fitWindow(animate: Bool) {
    guard let window = view.window else { return }
    let height = RecoveryWindowLayout.height(showsAccountForm: model.stage == .account)
    guard window.frame.height != height else { return }
    let frame = RecoveryWindowLayout.frame(
      window.frame, height: height, visible: window.screen?.visibleFrame)
    window.setFrame(frame, display: true, animate: animate)
  }

  @objc private func windowBecameKey() { track(NSEvent.modifierFlags) }
  @objc private func windowResignedKey() { track([]) }

  /// Only keys held while this window is key count, so Shift can't stick
  /// after the window loses focus or a sheet opens over it.
  private func track(_ modifiers: NSEvent.ModifierFlags) {
    let held = view.window?.isKeyWindow == true ? modifiers : []
    guard held != heldModifiers else { return }
    heldModifiers = held
    refresh()
  }

  private var completionAction: CompletionAction {
    CompletionAction.current(
      modifiers: heldModifiers, accountAvailable: model.canShowRecoveryAccountForm)
  }

  private func refresh() {
    if !Thread.isMainThread {
      DispatchQueue.main.async { [weak self] in self?.refresh() }
      return
    }
    guard isViewLoaded else { return }
    if renderedStage != model.stage || renderedAuthorization != model.asksForAuthorization {
      let stageChanged = renderedStage != model.stage
      renderedStage = model.stage
      renderedAuthorization = model.asksForAuthorization
      buildStage()
      if stageChanged { fitWindow(animate: true) }
    }
    titleLabel.stringValue = stageTitle
    titleLabel.alignment = model.stage == .account ? .left : .center
    titleWidthConstraint?.constant = model.stage == .account ? 380 : 600
    // Title offsets balance each screen in the compact window.
    switch model.stage {
    case .installing: titleTopConstraint?.constant = 40
    case .complete: titleTopConstraint?.constant = model.accountNeedsAuthorization ? 28 : 48
    case .welcome, .account: titleTopConstraint?.constant = 28
    }
    progressIndicator?.doubleValue = model.progress * 100
    operationLabel?.stringValue = model.currentOperation + "…"
    primaryButton?.isEnabled =
      !model.checkingEnvironment && !model.restartingCompletedSetup
      && !model.restartingWithoutAccount
    if model.stage == .welcome && model.asksForAuthorization {
      let prompt = model.authorizationPrompt
      authorizationMessage?.stringValue = prompt.message
      // A disabled field loses focus; give it back once the check finishes.
      let wasEnabled = authorizationField?.isEnabled
      authorizationField?.isEnabled = model.canAuthorize
      if wasEnabled == false && model.canAuthorize { focusAuthorizationField() }
      // RecoveryPrimaryButton shows its title in capitals.
      primaryButton?.title = prompt.action.uppercased()
      cancelRepairButton?.isHidden = prompt.purpose != .repair
      cancelRepairButton?.isEnabled = prompt.purpose == .repair && model.canAuthorize
    }
    if model.stage == .complete, let restart = primaryButton {
      let action = completionAction
      // RecoveryPrimaryButton shows its title in capitals.
      let title = action.title.uppercased()
      if restart.title != title { restart.title = title }
      restart.action =
        action == .restart ? #selector(restartPressed) : #selector(showRecoveryAccountForm(_:))
    }
    debugButton?.title = model.debugEnabled ? "Disable Debug" : "Enable Debug"
    proxyButton?.title = model.proxyEnabled ? "Disable Proxy" : "Enable Proxy"
    accountButton?.title =
      model.creatingAccount
      ? "PREPARING…" : model.accountAlreadyStarted ? "CONTINUE" : "CREATE ACCOUNT"
    accountButton?.isEnabled = model.canCreateAccount
    if !model.accountOffered {
      skipButton?.title = model.restartingWithoutAccount ? "Restarting…" : "Skip, Restart Now"
    }
    skipButton?.isEnabled = !model.creatingAccount && !model.restartingWithoutAccount
    if let field = accountUsernameField {
      field.isEnabled =
        !model.accountAlreadyStarted && !model.creatingAccount && !model.restartingWithoutAccount
      if field.currentEditor() == nil && field.stringValue != model.accountUsername {
        field.stringValue = model.accountUsername
      }
    }
    accountPasswordField?.isEnabled = !model.creatingAccount && !model.restartingWithoutAccount
    accountConfirmationField?.isEnabled = !model.creatingAccount && !model.restartingWithoutAccount
    // The model drops typed passwords on Skip and failures; keep the native fields in step.
    for (field, value) in [
      (accountPasswordField, model.accountPassword),
      (accountConfirmationField, model.accountConfirmation),
    ] where value.isEmpty && field?.stringValue.isEmpty == false {
      field?.stringValue = ""
    }
    copyButton?.title = copyingLog ? "Copying…" : "Copy Log"
    copyButton?.isEnabled = !copyingLog
    detailsViewer?.update(
      text: log.text.isEmpty ? model.operations.joined(separator: "\n") : log.text)
    if (model.environmentIssue != nil || model.failure != nil) && !issueShowing
      && sheets.active == nil
    {
      presentIssue()
    } else if let notice = model.previewNotice, sheets.active == nil {
      let done = RecoveryPrimaryButton(
        title: "Done", target: self, action: #selector(donePanelPressed))
      done.keyEquivalent = "\r"
      present(
        RecoverySheet(
          title: notice.title, message: notice.message,
          width: 430, body: NSView(), bodyHeight: 0, buttons: [done]))
    }
  }

  private var stageTitle: String {
    switch model.stage {
    case .welcome:
      return model.asksForAuthorization ? model.authorizationPrompt.title : "Repair startup"
    case .installing:
      return model.recoveryInterface ? "Restoring startup software" : "Setting up Omarchy"
    case .account:
      return model.accountAlreadyStarted ? "Resume recovery setup" : "Create a recovery account"
    case .complete:
      if model.accountNeedsAuthorization { return "Authorize recovery account" }
      return model.recoveryInterface ? "Recovery complete" : "Installation complete"
    }
  }

  private func buildStage() {
    for child in content.subviews { child.removeFromSuperview() }
    for child in footer.subviews { child.removeFromSuperview() }
    primaryButton = nil
    debugButton = nil
    proxyButton = nil
    progressIndicator = nil
    operationLabel = nil
    accountUsernameField = nil
    accountPasswordField = nil
    accountConfirmationField = nil
    accountButton = nil
    skipButton = nil
    authorizationMessage = nil
    authorizationField = nil
    cancelRepairButton = nil
    heldModifiers = view.window?.isKeyWindow == true ? NSEvent.modifierFlags : []

    switch model.stage {
    case .welcome: buildWelcome()
    case .installing: buildInstalling()
    case .account: buildAccount()
    case .complete: buildComplete()
    }
    buildFooter()
  }

  private func buildWelcome() {
    guard model.asksForAuthorization else {
      let stack = vertical(spacing: 9)
      stack.addArrangedSubview(label("Restore the startup software for your Mac.", size: 15))
      stack.addArrangedSubview(
        label("Use the saved recovery files to repair your\nstartup software.", size: 13))
      place(stack)
      return
    }
    // Finish setup is the password request itself; refresh() fills in the copy.
    let stack = vertical(spacing: 16)
    let message = label("", size: 13)
    message.preferredMaxLayoutWidth = 470
    stack.addArrangedSubview(message)
    authorizationMessage = message
    let field = NSSecureTextField()
    field.placeholderString = "Password"
    field.delegate = self
    style(field)
    stack.addArrangedSubview(field)
    authorizationField = field
    place(stack)
    focusAuthorizationField()
  }

  private func focusAuthorizationField() {
    DispatchQueue.main.async { [weak self] in
      guard let self, let field = self.authorizationField, field.isEnabled else { return }
      self.view.window?.makeFirstResponder(field)
    }
  }

  private func buildInstalling() {
    let stack = vertical(spacing: 15)
    let progress = NSProgressIndicator()
    progress.isIndeterminate = false
    progress.minValue = 0
    progress.maxValue = 100
    progress.controlSize = .regular
    progress.style = .bar
    progress.translatesAutoresizingMaskIntoConstraints = false
    progress.widthAnchor.constraint(equalToConstant: 380).isActive = true
    stack.addArrangedSubview(progress)
    progressIndicator = progress
    let operation = label("", size: 12)
    stack.addArrangedSubview(operation)
    operationLabel = operation
    place(stack)
  }

  private func buildAccount() {
    let stack = vertical(spacing: 11)
    stack.alignment = .leading
    let message =
      model.accountAlreadyStarted
      ? "Verify your recovery account’s password to resume authorization.\n\nYour Linux login is separate."
      : "Create a separate recovery account for your Linux volume. Next, authorize it as a volume owner to manage Linux boot settings independently of your macOS account.\n\nYour Linux login is separate."
    let explanation = label(message, size: 12, alignment: .left)
    explanation.maximumNumberOfLines = 0
    explanation.preferredMaxLayoutWidth = 380
    explanation.widthAnchor.constraint(equalToConstant: 380).isActive = true
    stack.addArrangedSubview(explanation)

    let username = NSTextField()
    username.stringValue = model.accountUsername
    username.placeholderString = "omarchy-owner"
    username.delegate = self
    username.identifier = NSUserInterfaceItemIdentifier("accountUsername")
    stack.addArrangedSubview(fieldRow("Username", field: username))
    accountUsernameField = username

    let password = NSSecureTextField()
    password.placeholderString = "Password"
    password.delegate = self
    password.identifier = NSUserInterfaceItemIdentifier("accountPassword")
    stack.addArrangedSubview(fieldRow("Password", field: password))
    accountPasswordField = password

    let confirmation = NSSecureTextField()
    confirmation.placeholderString = "Retype password"
    confirmation.delegate = self
    confirmation.identifier = NSUserInterfaceItemIdentifier("accountConfirmation")
    stack.addArrangedSubview(fieldRow("Retype password", field: confirmation))
    accountConfirmationField = confirmation
    place(stack, top: 0)
    DispatchQueue.main.async { [weak self] in
      guard let self else { return }
      self.view.window?.makeFirstResponder(
        self.model.accountAlreadyStarted ? self.accountPasswordField : self.accountUsernameField)
    }
  }

  private func buildComplete() {
    let stack = vertical(spacing: 13)
    if model.accountNeedsAuthorization {
      stack.addArrangedSubview(
        label(
          "Select “" + RecoveryActions.volumeName
            + "”, click “Restart…”, then “Authorize Users…”.",
          size: 12))
      stack.addArrangedSubview(
        label(
          "Enter the credentials for your macOS account and the recovery account you just created.",
          size: 12))
    } else {
      stack.addArrangedSubview(label("Restart to start using Omarchy.", size: 12))
    }
    place(stack, top: 16)
  }

  private func buildFooter() {
    let center = NSStackView()
    let stacked = model.stage == .account
    center.orientation = stacked ? .vertical : .horizontal
    center.spacing = stacked ? 6 : 12
    center.alignment = stacked ? .centerX : .centerY
    center.translatesAutoresizingMaskIntoConstraints = false

    switch model.stage {
    case .welcome where model.asksForAuthorization:
      // Cancel appears only while repairing the startup disk.
      let cancel = RecoverySecondaryButton(
        title: "Cancel", target: self, action: #selector(cancelRepairPressed))
      cancel.keyEquivalent = "\u{1b}"
      center.addArrangedSubview(cancel)
      cancelRepairButton = cancel
      let submit = RecoveryPrimaryButton(
        title: model.authorizationPrompt.action, target: self,
        action: #selector(authorizePressed))
      submit.keyEquivalent = "\r"
      center.addArrangedSubview(submit)
      primaryButton = submit
    case .welcome:
      let start = RecoveryPrimaryButton(
        title: "Restore", target: self, action: #selector(startPressed))
      center.addArrangedSubview(start)
      primaryButton = start
      let debug = RecoverySecondaryButton(
        title: "Enable Debug", target: self, action: #selector(debugPressed))
      let proxy = RecoverySecondaryButton(
        title: "Enable Proxy", target: self, action: #selector(proxyPressed))
      center.addArrangedSubview(debug)
      center.addArrangedSubview(proxy)
      debugButton = debug
      proxyButton = proxy
    case .installing: break
    case .account:
      let account = RecoveryPrimaryButton(
        title: "Create Account", target: self, action: #selector(accountPressed))
      center.addArrangedSubview(account)
      accountButton = account
      let skip =
        model.accountOffered
        ? RecoveryTextButton(title: "Back", target: self, action: #selector(backPressed))
        : RecoveryTextButton(
          title: "Skip, Restart Now", target: self, action: #selector(skipPressed))
      center.addArrangedSubview(skip)
      skipButton = skip
    case .complete:
      let restart = RecoveryPrimaryButton(
        title: "Restart", target: self, action: #selector(restartPressed))
      center.addArrangedSubview(restart)
      primaryButton = restart
    }
    footer.addSubview(center)
    NSLayoutConstraint.activate([
      center.centerXAnchor.constraint(equalTo: footer.centerXAnchor),
      center.centerYAnchor.constraint(equalTo: footer.centerYAnchor),
    ])
  }

  private func fieldRow(_ title: String, field: NSTextField) -> NSStackView {
    let row = vertical(spacing: 4)
    row.alignment = .leading
    row.addArrangedSubview(label(title, size: 12, alignment: .left))
    style(field)
    row.addArrangedSubview(field)
    return row
  }

  private func style(_ field: NSTextField) {
    field.font = OmarchyTheme.body
    field.textColor = OmarchyTheme.text
    field.isBezeled = true
    field.bezelStyle = .roundedBezel
    field.focusRingType = .exterior
    field.translatesAutoresizingMaskIntoConstraints = false
    field.widthAnchor.constraint(equalToConstant: 380).isActive = true
    field.heightAnchor.constraint(equalToConstant: 28).isActive = true
  }

  private func vertical(spacing: CGFloat) -> NSStackView {
    let stack = NSStackView()
    stack.orientation = .vertical
    stack.alignment = .centerX
    stack.spacing = spacing
    stack.translatesAutoresizingMaskIntoConstraints = false
    return stack
  }

  private func label(
    _ text: String, size: CGFloat, alignment: NSTextAlignment = .center
  ) -> NSTextField {
    let field = NSTextField(labelWithString: text)
    field.font = OmarchyTheme.mono(size)
    field.textColor = OmarchyTheme.secondaryText
    field.alignment = alignment
    field.lineBreakMode = .byWordWrapping
    field.maximumNumberOfLines = 0
    return field
  }

  private func place(_ stack: NSStackView, top: CGFloat = 8) {
    content.addSubview(stack)
    NSLayoutConstraint.activate([
      stack.topAnchor.constraint(equalTo: content.topAnchor, constant: top),
      stack.centerXAnchor.constraint(equalTo: content.centerXAnchor),
      stack.widthAnchor.constraint(lessThanOrEqualTo: content.widthAnchor),
    ])
  }

  func controlTextDidChange(_ notification: Notification) {
    guard let field = notification.object as? NSTextField else { return }
    switch field.identifier?.rawValue {
    case "accountUsername": model.accountUsername = field.stringValue
    case "accountPassword": model.accountPassword = field.stringValue
    case "accountConfirmation": model.accountConfirmation = field.stringValue
    default: break
    }
  }

  func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
    // Tab and Shift-Tab step through the account fields in order.
    let fields = [accountUsernameField, accountPasswordField, accountConfirmationField]
      .compactMap { $0 }.filter(\.isEnabled)
    if selector == #selector(NSResponder.insertTab(_:))
      || selector == #selector(NSResponder.insertBacktab(_:)),
      let index = fields.firstIndex(where: { $0 === control })
    {
      let step = selector == #selector(NSResponder.insertTab(_:)) ? 1 : fields.count - 1
      view.window?.makeFirstResponder(fields[(index + step) % fields.count])
      return true
    }
    guard selector == #selector(NSResponder.insertNewline(_:)) else { return false }
    if control === authorizationField {
      authorizePressed()
    } else if control === accountUsernameField {
      view.window?.makeFirstResponder(accountPasswordField)
    } else if control === accountPasswordField {
      view.window?.makeFirstResponder(accountConfirmationField)
    } else if control === accountConfirmationField {
      if model.canCreateAccount { accountPressed() }
    } else {
      return false
    }
    return true
  }

  @objc private func startPressed() {
    LaunchTrace.mark("ui.restore.pressed")
    model.restore()
  }

  /// Install, or Shut Down while repairing the startup disk. The field is
  /// cleared as soon as its password is handed to the model.
  @objc private func authorizePressed() {
    guard model.canAuthorize, let field = authorizationField else { return }
    let credential = field.stringValue
    guard !credential.isEmpty else {
      view.window?.makeFirstResponder(field)
      return
    }
    field.stringValue = ""
    LaunchTrace.mark("ui.authorization.submitted")
    model.authorize(with: credential)
  }

  @objc private func cancelRepairPressed() {
    authorizationField?.stringValue = ""
    model.cancelStartupDiskRepair()
    focusAuthorizationField()
  }

  @objc private func debugPressed() { model.toggleBootFlag("debug") }
  @objc private func proxyPressed() { model.toggleBootFlag("proxy") }
  @objc private func accountPressed() {
    model.createRecoveryAccount()
    accountPasswordField?.stringValue = ""
    accountConfirmationField?.stringValue = ""
  }
  @objc private func skipPressed() {
    model.skipRecoveryAccountAndRestart()
    accountPasswordField?.stringValue = ""
    accountConfirmationField?.stringValue = ""
  }
  @objc private func backPressed() {
    model.returnToCompletion()
    accountPasswordField?.stringValue = ""
    accountConfirmationField?.stringValue = ""
  }
  /// Shift-modified Restart and Utilities → Create Recovery Account….
  @objc func showRecoveryAccountForm(_ sender: Any?) {
    guard sheets.active == nil else { return }
    model.showRecoveryAccountForm()
  }
  @objc private func restartPressed() {
    // Restarting without the optional account records the deferral, so a
    // later Recovery session can still offer it.
    if model.accountOffered {
      model.skipRecoveryAccountAndRestart()
    } else {
      model.finishSetup()
    }
  }

  @discardableResult private func present(_ panel: RecoverySheet) -> Bool {
    sheets.present(panel, on: view.window)
  }

  private func closePanel() { sheets.close() }

  /// Utilities → Show Details. A failure sheet gives way to the details, as
  /// its own Show details button once did; any other sheet keeps it disabled.
  @objc func showDetails(_ sender: Any?) {
    guard canShowDetails else { return }
    if issueShowing { issueCancelPressed() }
    presentDetails()
  }

  private var canShowDetails: Bool {
    // There is nothing to show before the first attempt has logged anything.
    guard model.stage != .welcome || !log.text.isEmpty else { return false }
    return sheets.active == nil || (issueShowing && !issueRepair)
  }

  func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
    switch menuItem.action {
    case #selector(showDetails(_:)): return canShowDetails
    case #selector(showRecoveryAccountForm(_:)):
      return model.canShowRecoveryAccountForm && sheets.active == nil
    default: return true
    }
  }

  private func presentDetails() {
    let viewer = LogTextView(
      text: log.text.isEmpty ? model.operations.joined(separator: "\n") : log.text)
    viewer.translatesAutoresizingMaskIntoConstraints = false
    viewer.widthAnchor.constraint(equalToConstant: 474).isActive = true
    detailsViewer = viewer
    let copy = RecoverySecondaryButton(
      title: "Copy Log", target: self, action: #selector(copyLogPressed))
    copyButton = copy
    let done = RecoveryPrimaryButton(
      title: "Done", target: self, action: #selector(donePanelPressed))
    done.keyEquivalent = "\r"
    let panel = RecoverySheet(
      title: "Installation details",
      message:
        "Boot security is reduced only for this Omarchy installation. Your macOS security settings are unchanged."
        + (log.truncated
          ? " Showing recent output. Copy Log includes this attempt’s full log." : ""),
      width: 550, body: viewer,
      bodyHeight: RecoveryWindowLayout.detailsLogHeight(
        windowHeight: view.window?.frame.height ?? RecoveryWindowLayout.compactHeight),
      buttons: [copy, done])
    present(panel)
  }

  @objc private func copyLogPressed() {
    guard !copyingLog else { return }
    copyingLog = true
    refresh()
    DispatchQueue.global(qos: .userInitiated).async { [weak self] in
      guard let self else { return }
      let result = Result { try self.log.fullText() }
      DispatchQueue.main.async {
        self.copyingLog = false
        self.refresh()
        switch result {
        case .success(let text):
          NSPasteboard.general.clearContents()
          NSPasteboard.general.setString(text, forType: .string)
        case .failure(let error): self.model.failure = error.localizedDescription
        }
      }
    }
  }

  @objc private func donePanelPressed() {
    model.previewNotice = nil
    closePanel()
  }

  private func presentIssue() {
    let issue = model.environmentIssue
    issueRepair = issue?.canRepairStartupSelection == true
    let buttons: [NSButton]
    if issueRepair {
      let cancel = RecoverySecondaryButton(
        title: "Cancel", target: self, action: #selector(issueCancelPressed))
      cancel.keyEquivalent = "\u{1b}"
      let repair = RecoveryPrimaryButton(
        title: "Set Startup Disk and Shut Down", target: self,
        action: #selector(issueRepairPressed))
      buttons = [cancel, repair]
    } else {
      // The details are in Utilities → Show Details, so OK is the only action.
      let done = RecoveryPrimaryButton(
        title: "OK", target: self, action: #selector(issueCancelPressed))
      done.keyEquivalent = "\r"
      buttons = [done]
    }
    let panel = RecoverySheet(
      title: issue?.title ?? "Setup could not finish",
      message: issue?.message ?? model.failure ?? "",
      width: 460, body: NSView(), bodyHeight: 0, buttons: buttons)
    issueShowing = present(panel)
  }

  @objc private func issueRepairPressed() {
    issueShowing = false
    model.environmentIssue = nil
    model.failure = nil
    closePanel()
    model.repairStartupDisk()
  }

  @objc private func issueCancelPressed() {
    issueShowing = false
    model.environmentIssue = nil
    model.failure = nil
    closePanel()
  }

}
