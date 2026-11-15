import SwiftUI

struct InsightsView: View {
    @Environment(AppSession.self) private var session
    @State private var viewModel = InsightsViewModel()
    @State private var question = "Why is my safe to spend this amount?"
    @State private var includeScenario = false

    private let suggestions = [
        "Why is my safe to spend this amount?",
        "Am I at overdraft risk?",
        "Where is my spending drifting?",
        "What if I cut discretionary spending 20%?",
    ]

    var body: some View {
        NavigationStack {
            Group {
                switch viewModel.state {
                case .idle, .loading:
                    ProgressView("Loading insights…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .failed(let message):
                    ContentUnavailableView(
                        "Couldn't load insights",
                        systemImage: "exclamationmark.triangle",
                        description: Text(message)
                    )
                case .loaded(let bundle):
                    content(bundle)
                }
            }
            .navigationTitle("Insights")
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
    private func content(_ bundle: InsightsBundle) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                coachingHero(bundle)
                behaviorSection(bundle)
                driftSection(bundle.drift)
                alertsSection(bundle.insights)
                copilotSection
            }
            .padding()
            .opacity(viewModel.isRefreshing ? 0.72 : 1)
        }
        .refreshable {
            await viewModel.load(session: session)
        }
    }

    private func coachingHero(_ bundle: InsightsBundle) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Personalized guidance")
                    .font(.headline)
                if bundle.insights.aiGenerated {
                    Text("AI-generated")
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Color.teal.opacity(0.15))
                        .foregroundStyle(.teal)
                        .clipShape(Capsule())
                }
                MetricInfoButton(title: "AI insights", explanation: MetricGlossary.aiInsight)
            }
            Text(bundle.insights.coaching)
                .font(.body)
            if !bundle.insights.alertExplanation.isEmpty {
                Text(bundle.insights.alertExplanation)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.teal.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private func behaviorSection(_ bundle: InsightsBundle) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Spending behavior")
                .font(.headline)
            if let behavior = bundle.behavior {
                Text(BehaviorLabel.display(behavior.segment))
                    .font(.title3.weight(.semibold))
                ForEach(behavior.tips.prefix(3), id: \.self) { tip in
                    Label(tip, systemImage: "lightbulb")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            } else if let segment = bundle.insights.behaviorSegment {
                Text(BehaviorLabel.display(segment))
                    .font(.title3.weight(.semibold))
            }
        }
    }

    private func driftSection(_ drift: SpendingDriftResponse?) -> some View {
        Group {
            if let drift {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("Spending trend")
                            .font(.headline)
                        MetricInfoButton(title: "Spending drift", explanation: MetricGlossary.spendingDrift)
                    }
                    Text(driftDirectionLabel(drift.direction))
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.secondary.opacity(0.12))
                        .clipShape(Capsule())
                    MoneyRow(title: "Last \(drift.recentDays) days", value: drift.recentSpend)
                    MoneyRow(title: "Typical pace", value: drift.baselineSpend)
                    Text("Difference \(drift.driftAmount.formatted(.currency(code: "USD"))) (\(drift.driftPct.formatted())%).")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func alertsSection(_ insights: InsightsResponse) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Alerts")
                    .font(.headline)
                MetricInfoButton(
                    title: "Alerts",
                    explanation: "Computed from your balances, bills, and spending history — not invented by AI."
                )
            }
            if insights.alerts.isEmpty {
                Text("Nothing urgent right now.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(insights.alerts) { alert in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(alert.severity.uppercased())
                                .font(.caption2.weight(.bold))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(severityColor(alert.severity).opacity(0.2))
                                .clipShape(Capsule())
                            Text(alert.title)
                                .font(.subheadline.weight(.semibold))
                        }
                        Text(alert.summary)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private var copilotSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Ask Centsible")
                    .font(.headline)
                MetricInfoButton(title: "Copilot", explanation: MetricGlossary.aiInsight)
            }
            Text("Answers use your real forecast numbers. Ask about runway, bills, or spending.")
                .font(.footnote)
                .foregroundStyle(.secondary)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack {
                    ForEach(suggestions, id: \.self) { suggestion in
                        Button(suggestion) {
                            question = suggestion
                            includeScenario = suggestion.lowercased().contains("cut")
                                || suggestion.lowercased().contains("what if")
                        }
                        .buttonStyle(.bordered)
                        .font(.caption)
                    }
                }
            }

            Toggle("Compare a what-if scenario", isOn: $includeScenario)
            TextField("Ask a question…", text: $question, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(2...4)

            Button {
                Task {
                    await viewModel.ask(
                        question: question,
                        includeScenario: includeScenario,
                        session: session
                    )
                }
            } label: {
                if viewModel.isAsking {
                    ProgressView()
                } else {
                    Text("Ask")
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isAsking)

            if let reply = viewModel.copilotReply {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Answer")
                            .font(.subheadline.weight(.semibold))
                        if reply.aiGenerated {
                            Text("AI-generated")
                                .font(.caption2)
                                .foregroundStyle(.teal)
                        }
                        if reply.refused {
                            Text("Couldn't answer from your data")
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(.orange)
                        }
                    }
                    Text(reply.answer)
                    if let note = reply.scenarioNote {
                        Text(note)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    if let sts = reply.groundedMetrics["safe_to_spend"] {
                        Text("Based on safe-to-spend \(sts)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.top, 4)
            }

            if let error = viewModel.copilotError {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
    }

    private func severityColor(_ severity: String) -> Color {
        switch severity.lowercased() {
        case "urgent": return .red
        case "watch": return .orange
        default: return .blue
        }
    }

    private func driftDirectionLabel(_ direction: String) -> String {
        switch direction.lowercased() {
        case "up": return "Spending up"
        case "down": return "Spending down"
        default: return "Steady"
        }
    }
}

struct InsightsBundle: Sendable {
    var insights: InsightsResponse
    var drift: SpendingDriftResponse?
    var behavior: BehaviorSegmentResponse?
}

@Observable
final class InsightsViewModel {
    enum State {
        case idle
        case loading
        case loaded(InsightsBundle)
        case failed(String)
    }

    var state: State = .idle
    var isRefreshing = false
    var copilotReply: CopilotResponse?
    var copilotError: String?
    var isAsking = false

    private let client = APIClient()

    @MainActor
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
            async let insights = client.fetchInsights(horizonDays: horizon, minimumBuffer: buffer)
            async let drift = client.fetchSpendingDrift()
            async let behavior = client.fetchBehavior()
            let insightsBody = try await insights
            state = .loaded(
                InsightsBundle(
                    insights: insightsBody,
                    drift: try? await drift,
                    behavior: try? await behavior
                )
            )
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    @MainActor
    func ask(question: String, includeScenario: Bool, session: AppSession) async {
        isAsking = true
        copilotError = nil
        defer { isAsking = false }
        do {
            copilotReply = try await client.postCopilot(
                question: question,
                includeScenario: includeScenario,
                horizonDays: session.forecastHorizonDays,
                minimumBuffer: session.minimumBuffer
            )
        } catch {
            copilotError = error.localizedDescription
        }
    }
}

#Preview {
    InsightsView()
        .environment(AppSession())
}
