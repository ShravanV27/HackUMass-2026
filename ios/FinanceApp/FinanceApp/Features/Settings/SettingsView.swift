import SwiftUI

struct SettingsView: View {
    @Environment(AppSession.self) private var session
    @State private var viewModel = SettingsViewModel()
    @State private var cadence = "none"
    @State private var nextPayday = ""
    @State private var paycheckText = "0.00"
    @State private var bufferText = "250.00"

    var body: some View {
        NavigationStack {
            Form {
                if viewModel.isLoading {
                    Section {
                        ProgressView("Loading preferences…")
                    }
                }
                if let loadError = viewModel.loadError {
                    Section {
                        Text(loadError)
                            .font(.footnote)
                            .foregroundStyle(.red)
                        Button("Retry") {
                            Task {
                                await viewModel.load(session: session)
                                applyLoaded()
                            }
                        }
                    }
                }
                Section {
                    Text("Centsible preferences shape forecasts and safe-to-spend. Demo Mode lives here so everyday screens stay customer-focused.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Forecast") {
                    Picker("Lookahead", selection: Binding(
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
                    HStack {
                        Text("Safety buffer")
                        MetricInfoButton(
                            title: "Safety buffer",
                            explanation: "Cash you want to keep untouched. Safe-to-spend subtracts this amount after bills and estimated spending."
                        )
                        Spacer()
                        TextField("Buffer", text: $bufferText)
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                            .frame(maxWidth: 120)
                    }
                    Button("Save buffer") {
                        if let value = Decimal(string: bufferText.replacingOccurrences(of: ",", with: "")) {
                            session.minimumBuffer = value
                            Task { await viewModel.saveBuffer(session: session, cadence: cadence, nextPayday: nextPayday, paycheckText: paycheckText) }
                        }
                    }
                }

                Section {
                    Picker("Pay cycle", selection: $cadence) {
                        Text("None (no future income)").tag("none")
                        Text("Every 2 weeks").tag("biweekly")
                        Text("Twice a month").tag("semimonthly")
                        Text("Monthly").tag("monthly")
                    }
                    if cadence != "none" {
                        TextField("Next payday (YYYY-MM-DD)", text: $nextPayday)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        TextField("Paycheck amount", text: $paycheckText)
                            .keyboardType(.decimalPad)
                    }
                    Button("Save payment cycle") {
                        Task {
                            await viewModel.saveCycles(
                                session: session,
                                cadence: cadence,
                                nextPayday: nextPayday,
                                paycheckText: paycheckText,
                                bufferText: bufferText
                            )
                        }
                    }
                    if let message = viewModel.saveMessage {
                        Text(message)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    if let error = viewModel.saveError {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(.red)
                    }
                } header: {
                    HStack {
                        Text("Payment cycle")
                        MetricInfoButton(
                            title: "Payment cycle",
                            explanation: "Paydays are the only future income Centsible assumes. Leave as None if you do not want income projected."
                        )
                    }
                }

                Section("Demo Mode") {
                    Text("Practice with realistic banking activity. Changes update Home, Insights, and What If? immediately. Hypotheticals never write to your accounts — use What If? for that.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    NavigationLink("Open Demo Mode") {
                        DemoLabView(embeddedInSettings: true)
                    }
                }

                Section {
                    Toggle("Show developer details", isOn: Binding(
                        get: { session.showDeveloperDetails },
                        set: { session.showDeveloperDetails = $0 }
                    ))
                    if session.showDeveloperDetails {
                        LabeledContent("App mode", value: viewModel.modeLabel)
                        if !session.boundNessieCustomerId.isEmpty {
                            LabeledContent("Linked bank id", value: session.boundNessieCustomerId)
                        }
                        Text(viewModel.statusDetail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Button("Refresh bank data") {
                            Task { await viewModel.sync() }
                        }
                    }
                } header: {
                    Text("Developer")
                } footer: {
                    Text("Technical details stay collapsed so everyday use feels like a personal finance app.")
                }

                if let syncMessage = viewModel.syncMessage {
                    Section {
                        Text(syncMessage)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Settings")
            .task {
                await viewModel.load(session: session)
                applyLoaded()
            }
        }
    }

    private func applyLoaded() {
        if let cycle = viewModel.cycle {
            cadence = cycle.cadence
            nextPayday = cycle.nextPayday ?? ""
            paycheckText = NSDecimalNumber(decimal: cycle.paycheckAmount).stringValue
            bufferText = NSDecimalNumber(decimal: session.minimumBuffer).stringValue
        } else {
            bufferText = NSDecimalNumber(decimal: session.minimumBuffer).stringValue
        }
    }
}

@MainActor
@Observable
final class SettingsViewModel {
    var modeLabel = "—"
    var statusDetail = ""
    var cycle: PaymentCycleConfig?
    var saveMessage: String?
    var saveError: String?
    var syncMessage: String?
    var isLoading = false
    var loadError: String?

    private let client = APIClient()

    func load(session: AppSession) async {
        saveError = nil
        loadError = nil
        isLoading = true
        defer { isLoading = false }
        do {
            async let health = client.fetchHealth()
            async let settings = client.fetchPaymentCycles()
            async let demo = client.fetchDemoStatus()
            let healthBody = try await health
            let settingsBody = try await settings
            let demoBody = try? await demo
            modeLabel = healthBody.mode.lowercased() == "live" ? "Connected" : "Sample data"
            statusDetail = demoBody?.message ?? ""
            cycle = settingsBody.paymentCycle
            if session.minimumBuffer == 250 {
                session.minimumBuffer = settingsBody.paymentCycle.minimumBuffer
            }
        } catch {
            loadError = error.localizedDescription
        }
    }

    func saveBuffer(session: AppSession, cadence: String, nextPayday: String, paycheckText: String) async {
        await saveCycles(
            session: session,
            cadence: cadence,
            nextPayday: nextPayday,
            paycheckText: paycheckText,
            bufferText: NSDecimalNumber(decimal: session.minimumBuffer).stringValue
        )
    }

    func saveCycles(
        session: AppSession,
        cadence: String,
        nextPayday: String,
        paycheckText: String,
        bufferText: String
    ) async {
        saveError = nil
        saveMessage = nil
        guard let paycheck = Decimal(string: paycheckText.replacingOccurrences(of: ",", with: "")),
              let buffer = Decimal(string: bufferText.replacingOccurrences(of: ",", with: ""))
        else {
            saveError = "Enter valid dollar amounts."
            return
        }
        session.minimumBuffer = buffer
        let payday = nextPayday.trimmingCharacters(in: .whitespacesAndNewlines)
        let config = PaymentCycleConfig(
            cadence: cadence,
            nextPayday: cadence == "none" || payday.isEmpty ? nil : payday,
            paycheckAmount: cadence == "none" ? 0 : paycheck,
            minimumBuffer: buffer
        )
        do {
            let result = try await client.putPaymentCycles(config)
            cycle = result.paymentCycle
            saveMessage = "Payment preferences saved. Home, Insights, and What If? are refreshing."
            NotificationCenter.default.post(name: .pulseDataDidChange, object: nil)
        } catch {
            saveError = error.localizedDescription
        }
    }

    func sync() async {
        syncMessage = nil
        do {
            let result = try await client.postSync()
            syncMessage = result.message
            NotificationCenter.default.post(name: .pulseDataDidChange, object: nil)
        } catch {
            syncMessage = error.localizedDescription
        }
    }
}

#Preview {
    SettingsView()
        .environment(AppSession())
}
