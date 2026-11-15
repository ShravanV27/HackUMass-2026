import Charts
import SwiftUI

struct WhatIfView: View {
    @Environment(AppSession.self) private var session
    @State private var viewModel = WhatIfViewModel()
    @State private var selectedPreset: WhatIfPreset = .cut20
    @State private var customAmount = "75.00"
    @State private var purchaseDateText = ""

    var body: some View {
        NavigationStack {
            Group {
                switch viewModel.state {
                case .idle, .loading:
                    ProgressView("Building scenarios…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .failed(let message):
                    ContentUnavailableView(
                        "Couldn't run simulation",
                        systemImage: "exclamationmark.triangle",
                        description: Text(message)
                    )
                case .loaded(let bundle):
                    content(bundle)
                }
            }
            .navigationTitle("What If?")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if viewModel.isRefreshing {
                        ProgressView()
                    } else {
                        Button("Reset") {
                            Task { await viewModel.loadBaseline(session: session) }
                        }
                    }
                }
            }
            .task { await viewModel.loadBaseline(session: session) }
            .onReceive(NotificationCenter.default.publisher(for: .pulseDataDidChange)) { _ in
                Task { await viewModel.loadBaseline(session: session) }
            }
        }
    }

    @ViewBuilder
    private func content(_ bundle: WhatIfBundle) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                Text("Explore decisions without touching your real accounts. Simulations are hypothetical only.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Picker("Horizon", selection: Binding(
                    get: { session.forecastHorizonDays },
                    set: { value in
                        session.forecastHorizonDays = value
                        NotificationCenter.default.post(name: .pulseDataDidChange, object: nil)
                    }
                )) {
                    Text("7d").tag(7)
                    Text("14d").tag(14)
                    Text("30d").tag(30)
                }
                .pickerStyle(.segmented)

                presetsSection

                comparisonChart(bundle)
                impactCards(bundle)
                recommendations(bundle)

                if let purchase = bundle.purchaseWhatIf {
                    purchaseImpact(purchase)
                }
            }
            .padding()
            .opacity(viewModel.isRefreshing ? 0.72 : 1)
        }
        .scrollDismissesKeyboard(.interactively)
        .refreshable {
            await viewModel.run(
                preset: selectedPreset,
                amountText: customAmount,
                purchaseDateText: purchaseDateText,
                session: session
            )
        }
    }

    private var presetsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Try a decision")
                .font(.headline)

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                ForEach(WhatIfPreset.allCases) { preset in
                    Button {
                        selectedPreset = preset
                        Task {
                            await viewModel.run(
                                preset: preset,
                                amountText: customAmount,
                                purchaseDateText: purchaseDateText,
                                session: session
                            )
                        }
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(preset.title)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.primary)
                            Text(preset.subtitle)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .multilineTextAlignment(.leading)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(
                            selectedPreset == preset
                                ? Color(red: 0.12, green: 0.35, blue: 0.38).opacity(0.12)
                                : Color.secondary.opacity(0.08)
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                    .buttonStyle(.plain)
                }
            }

            if selectedPreset.needsAmount {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        DecimalAmountField(placeholder: "Amount", text: $customAmount, bordered: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button("Apply") {
                            Task {
                                await viewModel.run(
                                    preset: selectedPreset,
                                    amountText: customAmount,
                                    purchaseDateText: purchaseDateText,
                                    session: session
                                )
                            }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    if selectedPreset == .oneTimePurchase {
                        QuietEntryField(
                            placeholder: "Purchase date (optional YYYY-MM-DD)",
                            text: $purchaseDateText,
                            keyboard: .numbersAndPunctuation,
                            bordered: true
                        )
                        Text("Leave blank for today. A date inside the horizon moves the purchase on the chart. A date past the horizon is not applied.")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func comparisonChart(_ bundle: WhatIfBundle) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Balance outlook")
                    .font(.headline)
                MetricInfoButton(
                    title: "Scenario chart",
                    explanation: "Baseline is your current forecast. The second line is a hypothetical future. No bank data is changed."
                )
            }

            Chart {
                ForEach(bundle.baseline.points) { point in
                    if let day = ChartDate.parse(point.date) {
                        LineMark(
                            x: .value("Day", day),
                            y: .value("Balance", NSDecimalNumber(decimal: point.projectedBalance).doubleValue),
                            series: .value("Series", "Baseline")
                        )
                        .foregroundStyle(.secondary)
                        .lineStyle(StrokeStyle(lineWidth: 2, dash: [4, 3]))
                    }
                }
                ForEach(bundle.simulated.points) { point in
                    if let day = ChartDate.parse(point.date) {
                        LineMark(
                            x: .value("Day", day),
                            y: .value("Balance", NSDecimalNumber(decimal: point.projectedBalance).doubleValue),
                            series: .value("Series", bundle.simulated.label)
                        )
                        .foregroundStyle(Color(red: 0.12, green: 0.35, blue: 0.38))
                        .lineStyle(StrokeStyle(lineWidth: 2.5))
                    }
                }
            }
            .frame(height: 220)
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: max(1, bundle.horizonDays / 5))) { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let date = value.as(Date.self) {
                            Text(date, format: ChartDate.axisFormat)
                        }
                    }
                }
            }
            .chartLegend(position: .bottom, alignment: .leading)

            Text(bundle.simulated.note)
                .font(.footnote)
                .foregroundStyle(.secondary)

            if !bundle.assumptions.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Why this forecast")
                        .font(.caption.weight(.semibold))
                    ForEach(bundle.assumptions.prefix(4), id: \.self) { note in
                        Text("• \(note)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func impactCards(_ bundle: WhatIfBundle) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Impact vs today")
                .font(.headline)

            impactRow(
                title: "Safe to spend",
                before: bundle.baseline.safeToSpend,
                after: bundle.simulated.safeToSpend,
                info: MetricGlossary.safeToSpend
            )
            impactRow(
                title: "Lowest balance",
                before: bundle.baseline.minBalance,
                after: bundle.simulated.minBalance,
                info: "Lowest projected cash during the horizon."
            )

            HStack {
                Text("Overdraft outlook")
                MetricInfoButton(title: "Overdraft risk", explanation: MetricGlossary.overdraftRisk)
                Spacer()
                Text("\(RiskTone.label(for: bundle.baseline.overdraftRisk)) → \(RiskTone.label(for: bundle.simulated.overdraftRisk))")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(RiskTone.color(for: bundle.simulated.overdraftRisk))
            }
        }
    }

    private func impactRow(title: String, before: Decimal, after: Decimal, info: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                MetricInfoButton(title: title, explanation: info)
                Spacer()
                let delta = after - before
                Text(delta, format: .currency(code: "USD"))
                    .foregroundStyle(delta >= 0 ? .green : .red)
                    .font(.subheadline.weight(.semibold))
            }
            HStack {
                Text(before, format: .currency(code: "USD"))
                    .foregroundStyle(.secondary)
                Image(systemName: "arrow.right")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(after, format: .currency(code: "USD"))
                    .fontWeight(.semibold)
            }
            .font(.subheadline)
        }
        .padding(.vertical, 4)
    }

    private func recommendations(_ bundle: WhatIfBundle) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Recommendation")
                .font(.headline)
            Text(bundle.recommendation)
                .font(.body)
                .foregroundStyle(.secondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func purchaseVerdict(_ result: WhatIfResponse) -> (text: String, color: Color) {
        if !result.purchaseApplied {
            return ("Outside this horizon, so safe-to-spend is unchanged.", .secondary)
        }
        if result.affordable {
            return ("Looks affordable with your buffer.", .green)
        }
        return ("Would squeeze your safety buffer.", .red)
    }

    private func purchaseImpact(_ result: WhatIfResponse) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Purchase check")
                .font(.headline)
            Text(purchaseVerdict(result).text)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(purchaseVerdict(result).color)
            MoneyRow(title: "Safe before", value: result.safeToSpendBefore)
            MoneyRow(title: "Safe after", value: result.safeToSpendAfter)
            Text(result.note)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }
}

enum WhatIfPreset: String, CaseIterable, Identifiable {
    case cut20
    case aggressiveSave
    case extraIncome
    case oneTimePurchase
    case pauseDining

    var id: String { rawValue }

    var title: String {
        switch self {
        case .cut20: return "Cut spending 20%"
        case .aggressiveSave: return "Save harder"
        case .extraIncome: return "Extra income"
        case .oneTimePurchase: return "Big purchase"
        case .pauseDining: return "Pause dining out"
        }
    }

    var subtitle: String {
        switch self {
        case .cut20: return "Reduce discretionary spend"
        case .aggressiveSave: return "Cut deeper for the horizon"
        case .extraIncome: return "Add a one-time deposit"
        case .oneTimePurchase: return "Simulate a purchase"
        case .pauseDining: return "Hold dining category"
        }
    }

    var needsAmount: Bool {
        self == .extraIncome || self == .oneTimePurchase
    }

    var scenarioPayload: [String: Any] {
        switch self {
        case .cut20:
            return ["name": "cut_discretionary_20"]
        case .aggressiveSave:
            return ["name": "aggressive_save"]
        case .extraIncome:
            return ["name": "extra_paycheck"]
        case .oneTimePurchase:
            return ["name": "one_time_purchase"]
        case .pauseDining:
            return ["name": "pause_category", "category": "dining"]
        }
    }
}

struct WhatIfBundle: Sendable {
    var horizonDays: Int
    var baseline: ScenarioSeries
    var simulated: ScenarioSeries
    var purchaseWhatIf: WhatIfResponse?
    var recommendation: String
    var assumptions: [String]
}

@MainActor
@Observable
final class WhatIfViewModel {
    enum State {
        case idle
        case loading
        case loaded(WhatIfBundle)
        case failed(String)
    }

    private let client = APIClient()
    var state: State = .idle
    var isRefreshing = false

    func loadBaseline(session: AppSession) async {
        await run(preset: .cut20, amountText: "75.00", purchaseDateText: "", session: session)
    }

    func run(
        preset: WhatIfPreset,
        amountText: String,
        purchaseDateText: String,
        session: AppSession
    ) async {
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
        var scenario = preset.scenarioPayload
        let amount = Decimal(string: amountText.replacingOccurrences(of: ",", with: "")) ?? 75
        let purchaseDate = purchaseDateText.trimmingCharacters(in: .whitespacesAndNewlines)
        if preset.needsAmount {
            scenario["amount"] = NSDecimalNumber(decimal: amount).stringValue
        }
        if preset == .oneTimePurchase, !purchaseDate.isEmpty {
            scenario["on_date"] = purchaseDate
        }

        do {
            let simulation = try await client.postScenarios(
                scenarios: [
                    ["name": "baseline"],
                    scenario,
                ],
                horizonDays: horizon,
                minimumBuffer: buffer
            )
            guard let baseline = simulation.scenarios.first(where: { $0.name == "baseline" }),
                  let simulated = simulation.scenarios.last
            else {
                state = .failed("Couldn't compare futures — try Refresh.")
                return
            }

            var purchase: WhatIfResponse?
            if preset == .oneTimePurchase {
                purchase = try await client.postWhatIf(
                    amount: amount,
                    description: "What If? purchase",
                    horizonDays: horizon,
                    minimumBuffer: buffer,
                    purchaseDate: purchaseDate.isEmpty ? nil : purchaseDate
                )
            }

            state = .loaded(
                WhatIfBundle(
                    horizonDays: horizon,
                    baseline: baseline,
                    simulated: simulated,
                    purchaseWhatIf: purchase,
                    recommendation: Self.recommend(baseline: baseline, simulated: simulated, preset: preset),
                    assumptions: Self.assumptionLines(
                        shared: simulation.assumptions,
                        scenario: simulated.assumptions,
                        fallbackNote: simulated.note
                    )
                )
            )
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    /// Shared forecast notes, then lines that exist only on this scenario.
    /// Those extra lines are why this future differs from today.
    private static func assumptionLines(
        shared: [String],
        scenario: [String],
        fallbackNote: String
    ) -> [String] {
        let known = Set(shared)
        let whyChanged = scenario.filter { !known.contains($0) }
        if shared.isEmpty && whyChanged.isEmpty {
            return fallbackNote.isEmpty ? [] : [fallbackNote]
        }
        return whyChanged + shared
    }

    private static func recommend(baseline: ScenarioSeries, simulated: ScenarioSeries, preset: WhatIfPreset) -> String {
        let delta = simulated.deltaMinBalanceVsBaseline
        let improved = delta > 0
        let billHint = baseline.minBalance < simulated.minBalance
            ? "Protect that gain until after your next large bill."
            : "Review upcoming bills in Home before locking this in."
        switch preset {
        case .cut20, .aggressiveSave, .pauseDining:
            if improved {
                return "Cutting here lifts your lowest balance by \(delta.formatted(.currency(code: "USD"))). \(billHint)"
            }
            return "This cut barely moves runway. Try a larger reduction, pause a category, or move a bill date in Settings."
        case .extraIncome:
            return improved
                ? "Extra income adds \(delta.formatted(.currency(code: "USD"))) to your cushion. Route the surplus to the soonest bill first."
                : "Income helps, but bills still dominate — open Home and clear the next obligation."
        case .oneTimePurchase:
            if simulated.overdraftRisk == "likely" || simulated.safeToSpend < 0 {
                return "Skip or shrink this purchase — it pushes you toward overdraft. Wait for payday or cut elsewhere first."
            }
            if simulated.overdraftRisk == "watch" {
                return "You can buy it, but your buffer thins. Pair it with a spending cut or wait a few days."
            }
            return "This purchase fits your runway. Leave a little cushion for surprises."
        }
    }
}

#Preview {
    WhatIfView()
        .environment(AppSession())
}
