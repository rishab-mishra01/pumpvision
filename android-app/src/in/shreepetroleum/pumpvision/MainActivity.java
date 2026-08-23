package in.shreepetroleum.pumpvision;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.graphics.Bitmap;
import android.os.Bundle;
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

    private void showError() {
        showingError = true;
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
