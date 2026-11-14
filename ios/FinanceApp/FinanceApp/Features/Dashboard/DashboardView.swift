import Charts
import SwiftUI

struct DashboardView: View {
    @State private var viewModel = DashboardViewModel()

    var body: some View {
        NavigationStack {
            Group {
                switch viewModel.state {
                case .idle, .loading:
                    ProgressView(viewModel.loadingMessage)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .failed(let message):
                    ContentUnavailableView(
                        "Couldn’t load dashboard",
                        systemImage: "exclamationmark.triangle",
                        description: Text(message)
                    )
                case .loaded(let dashboard):
                    if dashboard.accounts.isEmpty {
                        ContentUnavailableView(
                            "No accounts yet",
                            systemImage: "building.columns",
                            description: Text(emptyDataCopy(for: dashboard.mode))
                        )
                    } else {
                        dashboardContent(dashboard)
                    }
                }
            }
            .navigationTitle("Finance Demo")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Refresh") {
                        Task { await viewModel.load() }
                    }
                }
            }
            .task {
                await viewModel.load()
            }
        }
    }

    @ViewBuilder
    private func dashboardContent(_ dashboard: DashboardResponse) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 8) {
                    modeBadge(dashboard.mode)
                    Text("Customer \(dashboard.customerId)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                Text(dataSourceCaption(for: dashboard.mode))
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                VStack(alignment: .leading, spacing: 8) {
                    Text("Accounts")
                        .font(.headline)
                    ForEach(dashboard.accounts) { account in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(account.name)
                                Text(account.accountType)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(account.balance, format: .currency(code: account.currency))
                                .fontWeight(.semibold)
                        }
                        .padding(.vertical, 4)
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("Money flow")
                        .font(.headline)
                    labeledRow("Income (posted)", dashboard.moneyFlow.incomePosted)
                    labeledRow("Expenses (posted)", dashboard.moneyFlow.expensesPosted)
                    labeledRow("Pending expenses", dashboard.moneyFlow.pendingExpenses)
                    labeledRow("Pending income", dashboard.moneyFlow.pendingIncome)
                    labeledRow("Net posted", dashboard.moneyFlow.netPosted)
                    labeledRow("Net incl. pending", dashboard.moneyFlow.netIncludingPending)
                    labeledRow("Transfer volume", dashboard.moneyFlow.transferVolume)
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("14-day forecast")
                        .font(.headline)
                    Chart {
                        ForEach(dashboard.forecast.points) { point in
                            if let day = ChartDate.parse(point.date) {
                                LineMark(
                                    x: .value("Day", day),
                                    y: .value(
                                        "Balance",
                                        NSDecimalNumber(decimal: point.projectedBalance).doubleValue
                                    )
                                )
                            }
                        }
                    }
                    .frame(height: 180)
                    .chartXAxis {
                        AxisMarks(values: .stride(by: .day, count: 3)) { value in
                            AxisGridLine()
                            AxisValueLabel {
                                if let date = value.as(Date.self) {
                                    Text(date, format: ChartDate.axisFormat)
                                }
                            }
                        }
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("Insight")
                        .font(.headline)
                    Text(dashboard.insight)
                        .font(.body)
                        .foregroundStyle(.secondary)
                }
            }
            .padding()
        }
    }

    private func modeBadge(_ mode: String) -> some View {
        Text(modeLabel(mode))
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color.secondary.opacity(0.15))
            .clipShape(Capsule())
    }

    private func modeLabel(_ mode: String) -> String {
        mode.lowercased() == "live" ? "LIVE · NESSIE SANDBOX" : "MOCK DATA"
    }

    private func dataSourceCaption(for mode: String) -> String {
        if mode.lowercased() == "live" {
            return "Balances and transactions synced from Nessie sandbox into TigerData. Credentials stay on the server."
        }
        return "Using local demo fixtures. Toggle USE_MOCK_DATA=false on the backend for live Nessie data."
    }

    private func emptyDataCopy(for mode: String) -> String {
        if mode.lowercased() == "live" {
            return "No accounts returned from the live Nessie sync. Check NESSIE_CUSTOMER_ID mapping and refresh."
        }
        return "Demo fixtures returned no accounts."
    }

    private func labeledRow(_ title: String, _ value: Decimal) -> some View {
        HStack {
            Text(title)
            Spacer()
            Text(value, format: .currency(code: "USD"))
        }
        .font(.subheadline)
    }
}

enum ChartDate {
    static let axisFormat = Date.FormatStyle()
        .month(.abbreviated)
        .day()

    static func parse(_ raw: String) -> Date? {
        let isoDay = Date.ISO8601FormatStyle().year().month().day()
        if let date = try? Date(raw, strategy: isoDay) {
            return date
        }
        return try? Date(raw, strategy: Date.ISO8601FormatStyle())
    }
}

@MainActor
@Observable
final class DashboardViewModel {
    enum State {
        case idle
        case loading
        case loaded(DashboardResponse)
        case failed(String)
    }

    private let client = APIClient()
    var state: State = .idle
    var loadingMessage = "Loading dashboard…"

    func load() async {
        state = .loading
        loadingMessage = "Loading dashboard…"
        do {
            let health = try? await client.fetchHealth()
            if let mode = health?.mode.lowercased() {
                loadingMessage = mode == "live"
                    ? "Loading live Nessie dashboard…"
                    : "Loading mock dashboard…"
            }
            let dashboard = try await client.fetchDashboard()
            state = .loaded(dashboard)
        } catch {
            state = .failed(error.localizedDescription)
        }
    }
}

#Preview {
    DashboardView()
}
