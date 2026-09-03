"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { messages, type Locale, type Messages } from "@/i18n/messages";

type Theme = "light" | "dark";

type AppState = {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: Messages;
  dir: "ltr" | "rtl";
  theme: Theme;
  toggleTheme: () => void;
  token: string | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useApp must be used inside <Providers>");
  return ctx;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState<Locale>("en");
  const [theme, setTheme] = useState<Theme>("light");
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // The access token lives in memory only. On load we exchange the httpOnly
  // refresh cookie for a new one, so a page reload keeps the session without
  // ever putting a credential in localStorage.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api<{ access_token: string }>("/auth/refresh", { method: "POST" });
        if (!cancelled) setToken(res.access_token);
      } catch {
        if (!cancelled) setToken(null);
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === "ar" ? "rtl" : "ltr";
  }, [locale]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setToken(res.access_token);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST", token });
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
    }
    setToken(null);
  }, [token]);

  const value = useMemo<AppState>(
    () => ({
      locale,
      setLocale,
      t: messages[locale] as unknown as Messages,
      dir: locale === "ar" ? "rtl" : "ltr",
      theme,
      toggleTheme: () => setTheme((v) => (v === "light" ? "dark" : "light")),
      token,
      ready,
      login,
      logout,
    }),
    [locale, theme, token, ready, login, logout],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
