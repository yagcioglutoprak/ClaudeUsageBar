class Aiquotabar < Formula
  desc "macOS menu bar app showing live Claude.ai and ChatGPT usage limits"
  homepage "https://github.com/yagcioglutoprak/AIQuotaBar"
  head "https://github.com/yagcioglutoprak/AIQuotaBar.git", branch: "main"

  license "MIT"

  depends_on "python@3.12"
  depends_on :macos => :monterey

  def install
    venv = libexec/"venv"
    system "python3.12", "-m", "venv", venv
    system venv/"bin/pip", "install", "--quiet", "rumps", "curl_cffi", "browser-cookie3"

    libexec.install "claude_bar.py"
    (libexec/"assets").mkpath
    (libexec/"assets").install Dir["assets/*"] if File.exist?("assets")

    # Fix rumps notification crash (requires CFBundleIdentifier)
    python_bin = venv/"bin"
    plist_path = python_bin.parent/"Info.plist"
    unless plist_path.exist?
      system "/usr/libexec/PlistBuddy", "-c",
             'Add :CFBundleIdentifier string "rumps"', plist_path.to_s
    end

    (bin/"aiquotabar").write <<~SH
      #!/bin/bash
      exec "#{venv}/bin/python" "#{libexec}/claude_bar.py" "$@"
    SH
    chmod 0755, bin/"aiquotabar"
  end

  def caveats
    <<~EOS
      AIQuotaBar is a macOS menu bar app. Launch it with:
        aiquotabar &

      To run it at login, click the ◆ icon in your menu bar → Launch at Login.

      Logs are written to: ~/.claude_bar.log
    EOS
  end

  test do
    system "#{libexec}/venv/bin/python", "-m", "py_compile", "#{libexec}/claude_bar.py"
  end
end
