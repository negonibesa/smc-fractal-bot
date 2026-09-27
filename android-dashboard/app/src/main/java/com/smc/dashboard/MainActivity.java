package com.smc.dashboard;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.webkit.HttpAuthHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

/**
 * Тонкая обёртка над дашбордом бота (Flask, порт 80).
 *
 * Две особенности этой установки, ради которых приложение и написано:
 *
 *  1) Дашборд закрыт HTTP Basic (DASH_USER/DASH_PASS). Без перехвата
 *     onReceivedHttpAuthRequest WebView показал бы браузерный запрос
 *     авторизации на каждом запуске. Здесь логин и пароль отдаются
 *     автоматически.
 *
 *  2) Дашборд отдаётся по голому HTTP. Android 9+ режет cleartext по
 *     умолчанию — разрешение вынесено в res/xml/network_security_config.xml.
 *
 * Пароль в репозиторий не кладётся: спрашивается один раз при первом
 * запуске и лежит в приватном хранилище приложения.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "smc_dashboard";
    private static final String K_URL = "url";
    private static final String K_USER = "user";
    private static final String K_PASS = "pass";
    private static final String DEFAULT_URL = "http://161.104.18.192";

    private static final int BG = 0xFF0B1220;
    private static final int MUTED = 0xFF94A3B8;

    private WebView web;
    private SwipeRefreshLayout swipe;
    private LinearLayout errorBox;
    private TextView errorText;
    private SharedPreferences prefs;
    private String user = "";
    private String pass = "";
    // onPageFinished вызывается и после неудачной загрузки (страница ошибки
    // тоже «загрузилась»). Без этого флага он спрятал бы экран ошибки сразу
    // после показа.
    private boolean loadFailed = false;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        user = prefs.getString(K_USER, "");
        pass = prefs.getString(K_PASS, "");

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);

        swipe = new SwipeRefreshLayout(this);
        web = new WebView(this);
        swipe.addView(web);
        root.addView(swipe, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        // Экран ошибки: без него неотвечающий сервер выглядит как белый
        // экран, и непонятно — бот лёг или телефон не видит сеть.
        errorBox = new LinearLayout(this);
        errorBox.setOrientation(LinearLayout.VERTICAL);
        errorBox.setGravity(Gravity.CENTER);
        errorBox.setBackgroundColor(BG);
        errorText = new TextView(this);
        errorText.setTextColor(MUTED);
        errorText.setTextSize(15);
        errorText.setGravity(Gravity.CENTER);
        errorText.setPadding(48, 48, 48, 48);
        Button retry = new Button(this);
        retry.setText("Повторить");
        retry.setOnClickListener(v -> {
            errorBox.setVisibility(View.GONE);
            swipe.setVisibility(View.VISIBLE);
            load();
        });
        errorBox.addView(errorText);
        errorBox.addView(retry);
        root.addView(errorBox, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        errorBox.setVisibility(View.GONE);

        setContentView(root);

        WebSettings st = web.getSettings();
        st.setJavaScriptEnabled(true);
        st.setDomStorageEnabled(true);
        st.setUseWideViewPort(true);
        st.setLoadWithOverviewMode(true);
        st.setSupportZoom(true);
        st.setBuiltInZoomControls(true);
        st.setDisplayZoomControls(false);
        st.setAllowFileAccess(false);
        st.setAllowContentAccess(false);
        st.setCacheMode(WebSettings.LOAD_DEFAULT);
        web.setBackgroundColor(BG);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler,
                                                   String host, String realm) {
                handler.proceed(user, pass);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                swipe.setRefreshing(false);
                if (!loadFailed) {
                    errorBox.setVisibility(View.GONE);
                    swipe.setVisibility(View.VISIBLE);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req,
                                        WebResourceError err) {
                if (req != null && !req.isForMainFrame()) {
                    return;
                }
                loadFailed = true;
                CharSequence d = err != null ? err.getDescription() : null;
                showError("Сервер не отвечает"
                        + (d != null ? "\n" + d : "")
                        + "\n\nПроверь адрес в настройках и что бот запущен.");
            }
        });

        swipe.setOnRefreshListener(() -> {
            if (web.getUrl() != null) {
                web.reload();
            } else {
                load();
            }
        });

        if (user.isEmpty()) {
            showSettings();
        } else {
            load();
        }
    }

    private void load() {
        String url = prefs.getString(K_URL, DEFAULT_URL);
        if (url == null || url.trim().isEmpty()) {
            url = DEFAULT_URL;
        }
        url = url.trim();
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        loadFailed = false;
        web.loadUrl(url);
    }

    private void showError(String msg) {
        swipe.setRefreshing(false);
        errorText.setText(msg);
        swipe.setVisibility(View.GONE);
        errorBox.setVisibility(View.VISIBLE);
    }

    private void showSettings() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int p = (int) (20 * getResources().getDisplayMetrics().density);
        box.setPadding(p, p, p, 0);

        EditText fUrl = new EditText(this);
        fUrl.setHint("Адрес, напр. http://161.104.18.192");
        fUrl.setText(prefs.getString(K_URL, DEFAULT_URL));
        fUrl.setSingleLine(true);
        fUrl.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);

        EditText fUser = new EditText(this);
        fUser.setHint("Пользователь (DASH_USER)");
        fUser.setText(prefs.getString(K_USER, ""));
        fUser.setSingleLine(true);
        fUser.setInputType(InputType.TYPE_CLASS_TEXT);

        EditText fPass = new EditText(this);
        fPass.setHint("Пароль (DASH_PASS)");
        fPass.setText(prefs.getString(K_PASS, ""));
        fPass.setSingleLine(true);
        fPass.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);

        box.addView(fUrl);
        box.addView(fUser);
        box.addView(fPass);

        new AlertDialog.Builder(this)
                .setTitle("Подключение к дашборду")
                .setView(box)
                .setCancelable(false)
                .setPositiveButton("Сохранить", (d, w) -> {
                    String url = fUrl.getText().toString().trim();
                    if (url.isEmpty()) {
                        url = DEFAULT_URL;
                    }
                    String u = fUser.getText().toString().trim();
                    String p2 = fPass.getText().toString();
                    prefs.edit()
                            .putString(K_URL, url)
                            .putString(K_USER, u)
                            .putString(K_PASS, p2)
                            .apply();
                    user = u;
                    pass = p2;
                    load();
                })
                .setNegativeButton("Выход", (d, w) -> finish())
                .show();
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add(0, 1, 0, "Обновить");
        menu.add(0, 2, 1, "Настройки");
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        if (item.getItemId() == 1) {
            web.reload();
            return true;
        }
        if (item.getItemId() == 2) {
            showSettings();
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
