package in.shreepetroleum.pumpvision;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.view.WindowInsetsController;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Pumpvision is a server-rendered Flask app reachable only over Tailscale.
 * This shell is a thin WebView around it -- there is no bundled web content, so
 * there is nothing to keep in sync with the server.
 *
 * Deliberately NOT a Trusted Web Activity: TWA verification uses Digital Asset
 * Links, which Android fetches from https://<host>/.well-known/assetlinks.json
 * on port 443 only. Pumpvision is served on :8443 because PIOS already owns the
 * root of :443 on the same host, so a TWA could never verify and would render a
 * URL bar anyway.
 */
public class MainActivity extends Activity {

    /** Tailscale MagicDNS name, not the 100.x address -- the IP can change. */
    private static final String APP_URL = "https://evo-x3-1.tail863296.ts.net:8443/";

    private WebView web;
    /** Set when a page load fails, so we don't push the error screen into history. */
    private boolean showingError = false;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        web = new WebView(this);
        web.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        // The app keeps the session in a cookie and uses localStorage for a few
        // per-device preferences (the Field-First skin opt-in), so both must persist.
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        // Server-rendered pages carry live figures; prefer the network and fall
        // back to cache only when offline.
        s.setCacheMode(WebSettings.LOAD_DEFAULT);

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // Everything lives on one origin; keep all navigation inside the app.
                return false;
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                if (!url.startsWith("file:///android_asset/")) {
                    showingError = false;
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (!showingError) {
                    syncStatusBarToPage();
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                // Only the main document matters -- a failed image must not
                // replace a page that otherwise rendered.
                if (request.isForMainFrame()) {
                    showError();
                }
            }
        });

        web.loadUrl(APP_URL);
    }

    /**
     * Match the status bar to the page's own <meta name="theme-color">.
     *
     * The three apps do not share one colour: owner/manager/attendant render on
     * parchment (#faf6ed), while the attendant's opt-in Field-First skin is white.
     * A single hardcoded statusBarColor is therefore wrong on one of them, and a
     * mismatched strip above the content reads as a rendering fault. Reading the
     * page keeps the shell honest without the shell knowing the palette.
     */
    private void syncStatusBarToPage() {
        web.evaluateJavascript(
            "(function(){var m=document.querySelector('meta[name=\"theme-color\"]');"
            + "return m?m.content:'';})()",
            value -> {
                if (value == null) return;
                String hex = value.replace("\"", "").trim();
                if (hex.isEmpty()) return;
                final int color;
                try {
                    color = Color.parseColor(hex);
                } catch (IllegalArgumentException e) {
                    return;   // page shipped something we cannot parse; keep the theme default
                }
                Window w = getWindow();
                w.setStatusBarColor(color);
                applyStatusBarIcons(w, isLight(color));
            });
    }

    /** Perceived luminance: dark icons belong on a light bar, and vice versa. */
    private static boolean isLight(int color) {
        double r = Color.red(color) / 255.0, g = Color.green(color) / 255.0, b = Color.blue(color) / 255.0;
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 0.5;
    }

    @SuppressWarnings("deprecation")
    private static void applyStatusBarIcons(Window w, boolean lightBackground) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowInsetsController c = w.getInsetsController();
            if (c != null) {
                c.setSystemBarsAppearance(
                        lightBackground ? WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS : 0,
                        WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS);
            }
            return;
        }
        // minSdk is 24, so the pre-R path still has to work.
        View d = w.getDecorView();
        int flags = d.getSystemUiVisibility();
        d.setSystemUiVisibility(lightBackground
                ? flags | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
                : flags & ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
    }

    private void showError() {
        showingError = true;
        // The offline screen is parchment; pin the bar to it rather than leaving
        // whatever the last real page happened to set.
        Window w = getWindow();
        w.setStatusBarColor(Color.parseColor("#faf6ed"));
        applyStatusBarIcons(w, true);
        web.loadUrl("file:///android_asset/offline.html");
    }

    /** Re-enter the live app from the error screen. */
    private void reload() {
        web.loadUrl(APP_URL);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (showingError) {
                // Back from the error screen retries rather than closing the app.
                reload();
                return true;
            }
            if (web.canGoBack()) {
                web.goBack();
                return true;
            }
        }
        return super.onKeyDown(keyCode, event);
    }
}
