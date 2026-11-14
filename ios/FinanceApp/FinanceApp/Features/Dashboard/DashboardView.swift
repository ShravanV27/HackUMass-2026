import Charts
import SwiftUI

struct DashboardView: View {
    @State private var viewModel = DashboardViewModel()

    var body: some View {
        NavigationStack {
            Group {
                switch viewModel.state {
                case .idle, .loading:
                    ProgressView("Loading demo dashboard…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .failed(let message):
                    ContentUnavailableView(
                        "Couldn’t load dashboard",
                        systemImage: "exclamationmark.triangle",
                        description: Text(message)
                    )
                case .loaded(let dashboard):
                    dashboardContent(dashboard)
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
                Text("Customer \(dashboard.customerId)")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)

                Text(dashboard.mode.uppercased())
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Color.secondary.opacity(0.15))
                    .clipShape(Capsule())

                VStack(alignment: .leading, spacing: 8) {
                    Text("Accounts")
                        .font(.headline)
                    ForEach(dashboard.accounts) { account in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(account.name)
                                Text(account.accountId)
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
                    Chart(dashboard.forecast.points) { point in
                        LineMark(
                            x: .value("Day", point.date),
                            y: .value("Balance", NSDecimalNumber(decimal: point.projectedBalance).doubleValue)
                        )
                    }
                    .frame(height: 180)
                    .chartXAxis {
                        AxisMarks(values: .automatic(desiredCount: 4))
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

    private func labeledRow(_ title: String, _ value: Decimal) -> some View {
        HStack {
            Text(title)
            Spacer()
            Text(value, format: .currency(code: "USD"))
        }
        .font(.subheadline)
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

    func load() async {
        state = .loading
        do {
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
