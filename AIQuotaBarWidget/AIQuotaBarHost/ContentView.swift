import SwiftUI

struct ContentView: View {
    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "gauge.with.dots.needle.33percent")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)

            Text("AI Quota Widget")
                .font(.title2.bold())

            Text("Add the widget to your desktop or Notification Center.")
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Divider()
                .padding(.horizontal, 40)

            VStack(alignment: .leading, spacing: 8) {
                Label("Right-click your desktop", systemImage: "cursorarrow.click.2")
                Label("Select \"Edit Widgets...\"", systemImage: "slider.horizontal.3")
                Label("Search for \"AI Quota\"", systemImage: "magnifyingglass")
            }
            .font(.callout)
            .foregroundStyle(.secondary)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
