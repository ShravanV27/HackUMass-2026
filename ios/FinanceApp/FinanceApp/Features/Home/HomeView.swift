import Charts
import SwiftUI

struct HomeView: View {
    @Environment(AppSession.self) private var session
    @State private var viewModel = HomeViewModel()

    var body: some View {
        NavigationStack {
            Group {
                switch viewModel.state {
                case .idle, .loading:
                    ProgressView("Checking your finances…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .failed(let message):
                    ContentUnavailableView(
                        "Couldn't load Home",
                        systemImage: "exclamationmark.triangle",
                        description: Text(message)
                    )
                case .loaded(let bundle):
                    content(bundle)
                }
            }
            .navigationTitle("Centsible")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if viewModel.isRefreshing {
                        ProgressView()
                    } else {
                        Button("Refresh") {
                            Task { await viewModel.load(session: session) }
                        }
                    }
                }
            }
            .task { await viewModel.load(session: session) }
            .onReceive(NotificationCenter.default.publisher(for: .pulseDataDidChange)) { _ in
                Task { await viewModel.load(session: session) }
            }
        }
    }

    @ViewBuilder
    private func content(_ bundle: HomeBundle) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                healthHero(bundle)
                assumptionsBanner(bundle.cashflow)
                alertsStrip(bundle.alerts)
                cashflowSection(bundle)
                billsSection(bundle.cashflow)
                accountsSection(bundle.accounts)
                activitySection(bundle.transactions)
            }
            .padding()
            .opacity(viewModel.isRefreshing ? 0.72 : 1)
        }
        .refreshable {
            await viewModel.load(session: session)
        }
    }

    private func healthHero(_ bundle: HomeBundle) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Financial health")
                    .font(.headline)
                MetricInfoButton(title: "Financial health", explanation: MetricGlossary.overdraftRisk)
                Spacer()
                Text(RiskTone.label(for: bundle.overdraft.overdraftRisk))
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(RiskTone.color(for: bundle.overdraft.overdraftRisk).opacity(0.15))
                    .foregroundStyle(RiskTone.color(for: bundle.overdraft.overdraftRisk))
                    .clipShape(Capsule())
            }

            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 4) {
                        Text("Safe to spend")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        MetricInfoButton(title: "Safe to spend", explanation: MetricGlossary.safeToSpend)
                    }
                    Text(max(bundle.safe.safeToSpend, Decimal(0)), format: .currency(code: "USD"))
                        .font(.system(size: 34, weight: .semibold, design: .rounded))
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 4) {
                    HStack(spacing: 4) {
                        Text("Cash runway")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        MetricInfoButton(
                            title: "Cash runway",
                            explanation: "Lowest projected balance over the next \(bundle.cashflow.horizonDays) days after known bills and typical spending."
                        )
                    }
                    Text(bundle.cashflow.minBalance, format: .currency(code: "USD"))
                        .font(.title3.weight(.semibold))
                }
            }

            if let days = bundle.overdraft.daysUntilZero {
                Text(days <= 0
                     ? "Cash is already at or below zero."
                     : "Projected to run out of cash in \(days) \(days == 1 ? "day" : "days") without changes.")
                    .font(.footnote)
                    .foregroundStyle(.red)
            } else if let days = bundle.overdraft.daysUntilBelowBuffer {
                Text(days <= 0
                     ? "Cash is already under your safety buffer."
                     : "May dip below your safety buffer in about \(days) \(days == 1 ? "day" : "days").")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            } else {
                Text("On track to stay above your \(bundle.safe.minimumBuffer.formatted(.currency(code: "USD"))) buffer.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Picker("Horizon", selection: Binding(
                get: { session.forecastHorizonDays },
                set: { newValue in
                    session.forecastHorizonDays = newValue
                    NotificationCenter.default.post(name: .pulseDataDidChange, object: nil)
                }
            )) {
                Text("7 days").tag(7)
                Text("14 days").tag(14)
                Text("30 days").tag(30)
            }
            .pickerStyle(.segmented)
        }
    }

    private func assumptionsBanner(_ cashflow: CashflowForecastResponse) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("How this forecast works")
                    .font(.subheadline.weight(.semibold))
                MetricInfoButton(
                    title: "Forecast assumptions",
                    explanation: MetricGlossary.forecastAssumptions
                )
                Spacer()
                Text(confidenceLabel(cashflow.confidence))
                    .font(.caption2.weight(.semibold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.secondary.opacity(0.12))
                    .clipShape(Capsule())
            }
            Text(cashflow.incomeAssumed
                 ? "Paydays from your Settings payment cycle are included."
                 : "No future paycheck is assumed until you set a payment cycle.")
                .font(.caption)
                .foregroundStyle(.secondary)
            if cashflow.confidence.lowercased() == "low" {
                Text("Risk flag: thin history, so this runway can move as new transactions post.")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
            if let trend = weekdaySpendLine(cashflow.weekdayRates) {
                Text(trend)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            ForEach(cashflow.assumptions.prefix(4), id: \.self) { note in
                Text("• \(note)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func weekdaySpendLine(_ rates: [String: Decimal]) -> String? {
        let order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        let labels = [
            "mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
            "fri": "Fri", "sat": "Sat", "sun": "Sun",
        ]
        let ranked = order.compactMap { key -> (String, Decimal)? in
            guard let value = rates[key] else { return nil }
            return (labels[key] ?? key, value)
        }.sorted { $0.1 > $1.1 }
        guard let busiest = ranked.first, let quietest = ranked.last, busiest.1 > quietest.1 else {
            return nil
        }
        return "Heavier typical cash spend on \(busiest.0) than \(quietest.0)."
    }

    private func confidenceLabel(_ raw: String) -> String {
        switch raw.lowercased() {
        case "high": return "Higher confidence"
        case "low": return "Lower confidence"
        default: return "Medium confidence"
        }
    }

    private func alertsStrip(_ alerts: [AlertItem]) -> some View {
        Group {
            if alerts.isEmpty {
                EmptyView()
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Needs attention")
                        .font(.headline)
                    ForEach(alerts.prefix(3)) { alert in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(alert.title)
                                .font(.subheadline.weight(.semibold))
                            Text(alert.summary)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(severityBackground(alert.severity))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                }
            }
        }
    }

    private func cashflowSection(_ bundle: HomeBundle) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Cash forecast")
                    .font(.headline)
                MetricInfoButton(
                    title: "Cash forecast",
                    explanation: "One predictive runway: known bills, weekday-aware spending, and paydays only when configured in Settings. Orange marks bill days; teal marks income."
                )
            }

            Chart {
                ForEach(bundle.cashflow.points) { point in
                    if let day = ChartDate.parse(point.date) {
                        LineMark(
                            x: .value("Day", day),
                            y: .value("Balance", NSDecimalNumber(decimal: point.projectedBalance).doubleValue)
                        )
                        .foregroundStyle(Color(red: 0.12, green: 0.35, blue: 0.38))
                        .interpolationMethod(.catmullRom)
                    }
                }
                ForEach(bundle.cashflow.points.filter { $0.scheduledBillTotal > 0 }) { point in
                    if let day = ChartDate.parse(point.date) {
                        PointMark(
                            x: .value("Day", day),
                            y: .value("Balance", NSDecimalNumber(decimal: point.projectedBalance).doubleValue)
                        )
                        .foregroundStyle(.orange)
                        .symbolSize(48)
                    }
                }
                ForEach(bundle.cashflow.points.filter { $0.projectedIncome > 0 }) { point in
                    if let day = ChartDate.parse(point.date) {
                        PointMark(
                            x: .value("Day", day),
                            y: .value("Balance", NSDecimalNumber(decimal: point.projectedBalance).doubleValue)
                        )
                        .foregroundStyle(.teal)
                        .symbolSize(48)
                    }
                }
            }
            .frame(height: 200)
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: max(1, bundle.cashflow.horizonDays / 5))) { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let date = value.as(Date.self) {
                            Text(date, format: ChartDate.axisFormat)
                        }
                    }
                }
            }

            HStack(spacing: 16) {
                legendDot(color: .orange, text: "Bills")
                legendDot(color: .teal, text: "Paydays")
                Spacer()
                Text("Ends \(bundle.cashflow.endingBalance.formatted(.currency(code: "USD")))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if !bundle.cashflow.nextEvents.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Upcoming")
                        .font(.subheadline.weight(.semibold))
                    ForEach(bundle.cashflow.nextEvents.prefix(4)) { event in
                        HStack {
                            Text(event.label)
                            Spacer()
                            Text(event.date)
                                .foregroundStyle(.secondary)
                        }
                        .font(.caption)
                    }
                }
            }
        }
    }

    private func billsSection(_ cashflow: CashflowForecastResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Upcoming bills")
                .font(.headline)
            if cashflow.scheduledBillsInHorizon.isEmpty {
                Text("No bills due in the next \(cashflow.horizonDays) days.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(cashflow.scheduledBillsInHorizon) { bill in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(bill.name)
                            Text("Due \(bill.nextDueDate)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(bill.amount, format: .currency(code: "USD"))
                            .fontWeight(.semibold)
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }

    private func accountsSection(_ accounts: [Account]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Your accounts")
                .font(.headline)
            if accounts.isEmpty {
                Text("No cash accounts yet. Add activity in Settings → Demo Mode, or refresh after connecting.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            ForEach(accounts) { account in
                HStack {
                    VStack(alignment: .leading) {
                        Text(friendlyAccountName(account))
                        Text(friendlyAccountType(account.accountType))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text(account.balance, format: .currency(code: account.currency))
                        .fontWeight(.semibold)
                }
            }
        }
    }

    private func activitySection(_ transactions: [BankTransaction]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Recent activity")
                .font(.headline)
            if transactions.isEmpty {
                Text("No recent transactions yet.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(transactions.prefix(8)) { txn in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(txn.description.isEmpty ? txn.category : txn.description)
                            Text("\(txn.category) · \(shortDate(txn.postedAt))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(txn.amount, format: .currency(code: "USD"))
                            .foregroundStyle(txn.amount >= 0 ? .green : .primary)
                    }
                }
            }
        }
    }

    private func legendDot(color: Color, text: String) -> some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(text).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func severityBackground(_ severity: String) -> Color {
        switch severity.lowercased() {
        case "urgent": return Color.red.opacity(0.12)
        case "watch": return Color.orange.opacity(0.12)
        default: return Color.blue.opacity(0.08)
        }
    }

    private func friendlyAccountName(_ account: Account) -> String {
        let name = account.name.trimmingCharacters(in: .whitespacesAndNewlines)
        return name.isEmpty ? "Account" : name
    }

    private func friendlyAccountType(_ type: String) -> String {
        switch type.lowercased() {
        case "checking": return "Checking"
        case "savings": return "Savings"
        case "credit card", "credit_card": return "Credit card"
        default: return type.capitalized
        }
    }

    private func shortDate(_ raw: String) -> String {
        if let date = ChartDate.parse(String(raw.prefix(10))) {
            return date.formatted(ChartDate.axisFormat)
        }
        return String(raw.prefix(10))
    }
}

struct HomeBundle: Sendable {
    var accounts: [Account]
    var transactions: [BankTransaction]
    var cashflow: CashflowForecastResponse
    var safe: SafeToSpendResponse
    var overdraft: OverdraftRiskResponse
    var alerts: [AlertItem]
}

@MainActor
@Observable
final class HomeViewModel {
    enum State {
        case idle
        case loading
        case loaded(HomeBundle)
        case failed(String)
    }

    private let client = APIClient()
    var state: State = .idle
    var isRefreshing = false

    func load(session: AppSession) async {
        let alreadyLoaded: Bool
        if case .loaded = state {
            alreadyLoaded = true
        } else {
            alreadyLoaded = false
        }
        if alreadyLoaded {
            isRefreshing = true
        } else {
            state = .loading
        }
        defer { isRefreshing = false }

        let horizon = session.forecastHorizonDays
        let buffer = session.minimumBuffer
        do {
            async let accounts = client.fetchAccounts()
            async let transactions = client.fetchTransactions()
            async let cashflow = client.fetchCashflowForecast(horizonDays: horizon)
            async let safe = client.fetchSafeToSpend(horizonDays: horizon, minimumBuffer: buffer)
            async let overdraft = client.fetchOverdraftRisk(horizonDays: horizon, minimumBuffer: buffer)
            async let alerts = client.fetchAlerts(horizonDays: horizon, minimumBuffer: buffer)

            let accountsBody = try await accounts
            let txnsBody = try await transactions
            let alertsBody = try? await alerts
            let cashAccounts = accountsBody.accounts.filter {
                !$0.accountType.lowercased().contains("credit")
            }

            state = .loaded(
                HomeBundle(
                    accounts: cashAccounts.isEmpty ? accountsBody.accounts : cashAccounts,
                    transactions: txnsBody.transactions.sorted { $0.postedAt > $1.postedAt },
                    cashflow: try await cashflow,
                    safe: try await safe,
                    overdraft: try await overdraft,
                    alerts: alertsBody?.alerts ?? []
                )
            )
        } catch {
            state = .failed(error.localizedDescription)
        }
    }
}

#Preview {
    HomeView()
        .environment(AppSession())
}
