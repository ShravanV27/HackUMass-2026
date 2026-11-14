import Charts
import SwiftUI

struct PredictionsView: View {
    @State private var viewModel = PredictionsViewModel()
    @State private var whatIfAmount = "50.00"

    var body: some View {
        NavigationStack {
            Group {
                switch viewModel.state {
                case .idle, .loading:
                    ProgressView(viewModel.loadingMessage)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .failed(let message):
                    ContentUnavailableView(
                        "Couldn’t load predictions",
                        systemImage: "exclamationmark.triangle",
                        description: Text(message)
                    )
                case .loaded(let bundle):
                    content(bundle)
                }
            }
            .navigationTitle("Predictions")
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
    private func content(_ bundle: PredictionsBundle) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack(spacing: 8) {
                    modeBadge(bundle.mode)
                    Text("as of \(bundle.cashflow.asOf)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Text(dataSourceCaption(for: bundle.mode))
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                safeToSpendSection(bundle.safe)
                cashflowSection(bundle.cashflow)
                scheduledBillsSection(bundle.cashflow, mode: bundle.mode)
                driftSection(bundle.drift)
                whatIfSection(bundle)
            }
            .padding()
        }
    }

    private func modeBadge(_ mode: String) -> some View {
        Text(mode.lowercased() == "live" ? "LIVE · NESSIE SANDBOX" : "MOCK DATA")
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color.secondary.opacity(0.15))
            .clipShape(Capsule())
    }

    private func dataSourceCaption(for mode: String) -> String {
        if mode.lowercased() == "live" {
            return "Forecasts use live Nessie balances, transactions, and bills. Confirmed bills are separate from estimated spend."
        }
        return "Forecasts use demo fixtures. Estimated spend is historical discretionary average — not invented income."
    }

    private func safeToSpendSection(_ safe: SafeToSpendResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Safe to spend")
                .font(.headline)
            Text(safe.safeToSpend, format: .currency(code: "USD"))
                .font(.largeTitle.weight(.semibold))
            Text("After pending items, known bills, estimated spend, and a \(safe.minimumBuffer.formatted(.currency(code: "USD"))) buffer. Credit-card debt is excluded from cash. No future income is assumed.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            labeledRow("Available cash", safe.availableBalance)
            labeledRow("Scheduled bills (horizon)", safe.scheduledBillsTotal)
            labeledRow("Estimated spend (horizon)", safe.estimatedSpendTotal)
            labeledRow("Pending expenses", safe.pendingExpenses)
        }
    }

    private func cashflowSection(_ cashflow: CashflowForecastResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("14-day cash flow")
                .font(.headline)
            Chart {
                ForEach(cashflow.points) { point in
                    if let day = ChartDate.parse(point.date) {
                        LineMark(
                            x: .value("Day", day),
                            y: .value(
                                "Balance",
                                NSDecimalNumber(decimal: point.projectedBalance).doubleValue
                            )
                        )
                        .foregroundStyle(.blue)
                    }
                }
                ForEach(cashflow.points.filter { $0.scheduledBillTotal > 0 }) { point in
                    if let day = ChartDate.parse(point.date) {
                        PointMark(
                            x: .value("Day", day),
                            y: .value(
                                "Balance",
                                NSDecimalNumber(decimal: point.projectedBalance).doubleValue
                            )
                        )
                        .foregroundStyle(.orange)
                    }
                }
            }
            .frame(height: 200)
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

            labeledRow("Starting cash", cashflow.startingBalance)
            labeledRow("Daily estimated spend", cashflow.dailyEstimatedSpend)
            labeledRow("Scheduled bills total", cashflow.scheduledBillsTotal)
            labeledRow("Estimated spend total", cashflow.estimatedSpendTotal)
            labeledRow("Min projected balance", cashflow.minBalance)
            Text("Orange points mark days with confirmed scheduled bills. Blue line is projected cash after bills + estimated discretionary spend.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private func scheduledBillsSection(_ cashflow: CashflowForecastResponse, mode: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Scheduled bills in horizon")
                .font(.headline)
            if cashflow.scheduledBillsInHorizon.isEmpty {
                Text(emptyBillsCopy(horizon: cashflow.horizonDays, mode: mode))
                    .foregroundStyle(.secondary)
            } else {
                ForEach(cashflow.scheduledBillsInHorizon) { bill in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(bill.name)
                            Text("Due \(bill.nextDueDate) · \(bill.category)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(bill.amount, format: .currency(code: "USD"))
                            .fontWeight(.semibold)
                    }
                }
            }
        }
    }

    private func emptyBillsCopy(horizon: Int, mode: String) -> String {
        if mode.lowercased() == "live" {
            return "No Nessie bills due in the next \(horizon) days. Forecasts still use estimated discretionary spend from history when available."
        }
        return "No known bills due in the next \(horizon) days."
    }

    private func driftSection(_ drift: SpendingDriftResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Spending drift")
                .font(.headline)
            Text(drift.direction.uppercased())
                .font(.caption.weight(.semibold))
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color.secondary.opacity(0.15))
                .clipShape(Capsule())
            labeledRow("Recent \(drift.recentDays)d spend", drift.recentSpend)
            labeledRow("Baseline (normalized)", drift.baselineSpend)
            labeledRow("Drift amount", drift.driftAmount)
            if drift.recentSpend == 0 && drift.baselineSpend == 0 {
                Text("No posted discretionary spend in the lookback windows yet.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                Text("Drift \(drift.driftPct.formatted())% vs prior \(drift.baselineDays)-day pace.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func whatIfSection(_ bundle: PredictionsBundle) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("What-if purchase")
                .font(.headline)
            TextField("Amount", text: $whatIfAmount)
                .keyboardType(.decimalPad)
                .textFieldStyle(.roundedBorder)
            Button("Simulate") {
                Task { await viewModel.runWhatIf(amountText: whatIfAmount) }
            }
            .buttonStyle(.borderedProminent)

            if let result = bundle.whatIf {
                Text(result.affordable ? "Affordable" : "Not affordable")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(result.affordable ? .green : .red)
                labeledRow("Safe before", result.safeToSpendBefore)
                labeledRow("Safe after", result.safeToSpendAfter)
                labeledRow("Min balance after", result.projectedMinBalanceAfter)
                Text(result.note)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
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

struct PredictionsBundle: Sendable {
    var mode: String
    var cashflow: CashflowForecastResponse
    var safe: SafeToSpendResponse
    var drift: SpendingDriftResponse
    var whatIf: WhatIfResponse?
}

@MainActor
@Observable
final class PredictionsViewModel {
    enum State {
        case idle
        case loading
        case loaded(PredictionsBundle)
        case failed(String)
    }

    private let client = APIClient()
    var state: State = .idle
    var loadingMessage = "Loading predictions…"

    func load() async {
        state = .loading
        loadingMessage = "Loading predictions…"
        do {
            let health = try await client.fetchHealth()
            loadingMessage = health.mode.lowercased() == "live"
                ? "Loading live predictions…"
                : "Loading mock predictions…"
            async let cashflow = client.fetchCashflowForecast()
            async let safe = client.fetchSafeToSpend()
            async let drift = client.fetchSpendingDrift()
            let bundle = PredictionsBundle(
                mode: health.mode,
                cashflow: try await cashflow,
                safe: try await safe,
                drift: try await drift,
                whatIf: nil
            )
            state = .loaded(bundle)
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    func runWhatIf(amountText: String) async {
        guard case .loaded(var bundle) = state else { return }
        let normalized = amountText.replacingOccurrences(of: ",", with: "")
        guard let amount = Decimal(string: normalized), amount > 0 else {
            state = .failed("Enter a valid purchase amount.")
            return
        }
        do {
            let result = try await client.postWhatIf(amount: amount, description: "Simulator what-if")
            bundle.whatIf = result
            state = .loaded(bundle)
        } catch {
            state = .failed(error.localizedDescription)
        }
    }
}

#Preview {
    PredictionsView()
}
