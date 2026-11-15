import SwiftUI

struct DemoLabView: View {
    var embeddedInSettings: Bool = false

    @Environment(AppSession.self) private var session
    @State private var viewModel = DemoLabViewModel()
    @State private var amountText = "50.00"
    @State private var descriptionText = "Bought groceries"
    @State private var payeeText = "Landlord"
    @State private var status = "posted"
    @State private var action: DemoLabAction = .purchase

    var body: some View {
        Group {
            switch viewModel.state {
            case .idle, .loading:
                ProgressView(viewModel.loadingMessage)
            case .failed(let message):
                ContentUnavailableView(
                    "Demo Mode unavailable",
                    systemImage: "wrench.and.screwdriver",
                    description: Text(message)
                )
            case .ready:
                formContent
            }
        }
        .navigationTitle(embeddedInSettings ? "Demo Mode" : "Demo Mode")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Refresh") {
                    Task { await viewModel.load() }
                }
            }
        }
        .task { await viewModel.load() }
    }

    @ViewBuilder
    private var formContent: some View {
        @Bindable var viewModel = viewModel
        Form {
            Section {
                Text("Record everyday banking activity. Your Home, Insights, and What If? screens update after each action. What If? simulations never write here.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                if !viewModel.writesEnabled {
                    Text("Demo writes are turned off on the server. Ask your teammate to enable Demo Mode, or continue exploring with sample data.")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }
            }

            Section("Quick actions") {
                ForEach(DemoPreset.allCases) { preset in
                    Button(preset.title) {
                        Task { await applyPreset(preset) }
                    }
                    .disabled(!viewModel.writesEnabled || viewModel.isSubmitting || viewModel.selectedAccountId.isEmpty)
                }
            }

            Section("Account") {
                if viewModel.accounts.isEmpty {
                    Text("No accounts loaded")
                } else {
                    Picker("From account", selection: $viewModel.selectedAccountId) {
                        ForEach(viewModel.accounts) { account in
                            Text("\(account.name) · \(account.balance, format: .currency(code: "USD"))")
                                .tag(account.accountId)
                        }
                    }
                }
            }

            Section("Custom activity") {
                Picker("Type", selection: $action) {
                    ForEach(DemoLabAction.allCases) { item in
                        Text(item.title).tag(item)
                    }
                }
                .pickerStyle(.segmented)

                TextField("Amount", text: $amountText)
                    .keyboardType(.decimalPad)

                if action == .bill {
                    TextField("Payee", text: $payeeText)
                } else {
                    TextField("Description", text: $descriptionText)
                    Picker("Status", selection: $status) {
                        Text("Posted").tag("posted")
                        Text("Pending").tag("pending")
                    }
                }

                Button {
                    Task { await submit() }
                } label: {
                    if viewModel.isSubmitting {
                        ProgressView()
                    } else {
                        Text(action.submitTitle)
                    }
                }
                .disabled(!viewModel.writesEnabled || viewModel.isSubmitting || viewModel.selectedAccountId.isEmpty)
            }

            if let impact = viewModel.lastImpact {
                Section("Before / after") {
                    impactRow(
                        "Safe to spend",
                        before: max(impact.before.safeToSpend, Decimal(0)),
                        after: max(impact.after.safeToSpend, Decimal(0)),
                        delta: impact.deltaSafeToSpend
                    )
                    impactRow("Lowest runway", before: impact.before.minProjectedBalance, after: impact.after.minProjectedBalance, delta: impact.deltaMinBalance)
                    LabeledContent("Health before", value: RiskTone.label(for: impact.before.overdraftRisk))
                    LabeledContent("Health after", value: RiskTone.label(for: impact.after.overdraftRisk))
                    Text(impact.note)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }

            if session.showDeveloperDetails {
                Section("Developer details") {
                    LabeledContent("Mode", value: viewModel.modeLabel)
                    LabeledContent("Writes", value: viewModel.writesEnabled ? "On" : "Off")
                    if let bound = viewModel.boundNessieId, !bound.isEmpty {
                        LabeledContent("Bank link id", value: bound)
                    }
                    Text(viewModel.statusMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Force refresh from bank") {
                        Task { await viewModel.syncNow() }
                    }
                    .disabled(viewModel.isSubmitting)
                }
            } else {
                Section {
                    Button("Refresh balances") {
                        Task { await viewModel.syncNow() }
                    }
                    .disabled(viewModel.isSubmitting)
                }
            }

            if let error = viewModel.actionError {
                Section {
                    Text(error)
                        .foregroundStyle(.red)
                        .font(.footnote)
                }
            }
        }
    }

    private func impactRow(_ title: String, before: Decimal, after: Decimal, delta: Decimal) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.subheadline.weight(.semibold))
            HStack {
                Text(before, format: .currency(code: "USD"))
                Image(systemName: "arrow.right")
                Text(after, format: .currency(code: "USD"))
                Spacer()
                Text(delta, format: .currency(code: "USD"))
                    .foregroundStyle(delta >= 0 ? .green : .red)
            }
            .font(.footnote)
        }
    }

    private func submit() async {
        guard let amount = Decimal(string: amountText) else {
            viewModel.actionError = "Enter a valid amount."
            return
        }
        switch action {
        case .purchase:
            await viewModel.purchase(
                amount: amount,
                description: descriptionText,
                status: status,
                session: session
            )
        case .deposit:
            await viewModel.deposit(
                amount: amount,
                description: descriptionText,
                status: status,
                session: session
            )
        case .bill:
            await viewModel.bill(amount: amount, payee: payeeText, session: session)
        }
    }

    private func applyPreset(_ preset: DemoPreset) async {
        switch preset {
        case .paycheck:
            await viewModel.deposit(
                amount: 1800,
                description: "Received paycheck",
                status: "posted",
                session: session
            )
        case .rent:
            await viewModel.bill(amount: 1200, payee: "Rent", session: session)
        case .groceries:
            await viewModel.purchase(
                amount: 64.50,
                description: "Bought groceries",
                status: "posted",
                session: session
            )
        case .coffee:
            await viewModel.purchase(
                amount: 6.75,
                description: "Coffee run",
                status: "posted",
                session: session
            )
        }
    }
}

enum DemoPreset: String, CaseIterable, Identifiable {
    case paycheck, rent, groceries, coffee
    var id: String { rawValue }
    var title: String {
        switch self {
        case .paycheck: return "Received paycheck"
        case .rent: return "Paid rent"
        case .groceries: return "Bought groceries"
        case .coffee: return "Coffee run"
        }
    }
}

enum DemoLabAction: String, CaseIterable, Identifiable {
    case purchase, deposit, bill
    var id: String { rawValue }
    var title: String {
        switch self {
        case .purchase: return "Purchase"
        case .deposit: return "Deposit"
        case .bill: return "Bill"
        }
    }
    var submitTitle: String {
        switch self {
        case .purchase: return "Record purchase"
        case .deposit: return "Record deposit"
        case .bill: return "Schedule bill"
        }
    }
}

@Observable
final class DemoLabViewModel {
    enum State {
        case idle
        case loading
        case ready
        case failed(String)
    }

    var state: State = .idle
    var loadingMessage = "Loading Demo Mode…"
    var writesEnabled = false
    var modeLabel = "—"
    var statusMessage = ""
    var boundNessieId: String?
    var accounts: [Account] = []
    var selectedAccountId = ""
    var lastImpact: DemoActionResponse?
    var actionError: String?
    var isSubmitting = false

    private let client = APIClient()

    @MainActor
    func load() async {
        state = .loading
        loadingMessage = "Loading Demo Mode…"
        actionError = nil
        do {
            async let status = client.fetchDemoStatus()
            async let accountsResponse = client.fetchAccounts()
            let demoStatus = try await status
            let accountsBody = try await accountsResponse
            writesEnabled = demoStatus.writesEnabled
            modeLabel = demoStatus.mode.uppercased()
            statusMessage = demoStatus.message
            boundNessieId = demoStatus.boundNessieCustomerId ?? demoStatus.envNessieCustomerId
            accounts = accountsBody.accounts.filter { !$0.accountType.lowercased().contains("credit") }
            if selectedAccountId.isEmpty {
                selectedAccountId = accounts.first?.accountId ?? ""
            }
            state = .ready
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    @MainActor
    func purchase(
        amount: Decimal,
        description: String,
        status: String,
        session: AppSession
    ) async {
        await run {
            try await client.postDemoPurchase(
                accountId: selectedAccountId,
                amount: amount,
                description: description,
                status: status,
                horizonDays: session.forecastHorizonDays,
                minimumBuffer: session.minimumBuffer
            )
        }
    }

    @MainActor
    func deposit(
        amount: Decimal,
        description: String,
        status: String,
        session: AppSession
    ) async {
        await run {
            try await client.postDemoDeposit(
                accountId: selectedAccountId,
                amount: amount,
                description: description,
                status: status,
                horizonDays: session.forecastHorizonDays,
                minimumBuffer: session.minimumBuffer
            )
        }
    }

    @MainActor
    func bill(amount: Decimal, payee: String, session: AppSession) async {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        let due = formatter.string(from: Calendar.current.date(byAdding: .day, value: 7, to: Date()) ?? Date())
        await run {
            try await client.postDemoBill(
                accountId: selectedAccountId,
                payee: payee,
                amount: amount,
                paymentDate: due,
                recurringDate: nil,
                horizonDays: session.forecastHorizonDays,
                minimumBuffer: session.minimumBuffer
            )
        }
    }

    @MainActor
    func syncNow() async {
        isSubmitting = true
        actionError = nil
        defer { isSubmitting = false }
        do {
            let result = try await client.postSync()
            statusMessage = result.message
            NotificationCenter.default.post(name: .pulseDataDidChange, object: nil)
            await load()
        } catch {
            actionError = error.localizedDescription
        }
    }

    @MainActor
    private func run(_ work: () async throws -> DemoActionResponse) async {
        isSubmitting = true
        actionError = nil
        defer { isSubmitting = false }
        do {
            lastImpact = try await work()
            let accountsBody = try await client.fetchAccounts()
            accounts = accountsBody.accounts.filter { !$0.accountType.lowercased().contains("credit") }
            NotificationCenter.default.post(name: .pulseDataDidChange, object: nil)
        } catch {
            actionError = error.localizedDescription
        }
    }
}

#Preview {
    NavigationStack {
        DemoLabView(embeddedInSettings: true)
            .environment(AppSession())
    }
}
