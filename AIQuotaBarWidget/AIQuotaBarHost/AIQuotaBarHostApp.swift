import SwiftUI
import WidgetKit

@main
struct AIQuotaBarHostApp: App {
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .onAppear {
                    // Reload widget timelines whenever the host app opens
                    WidgetCenter.shared.reloadAllTimelines()
                }
        }
        .defaultSize(width: 400, height: 300)
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                WidgetCenter.shared.reloadAllTimelines()
            }
        }
    }
}
