import { type ReactNode, createContext, useEffect } from "react";
import { ThemeProvider as NextThemesProvider, useTheme } from "next-themes";
import { setThemeSource } from "@/lib/nativeBridge";

/**
 * Mirrors the in-app theme selection onto native shell chrome. Renders nothing.
 */
function NativeThemeSync() {
  const { theme } = useTheme();
  useEffect(() => {
    if (theme === "light" || theme === "dark" || theme === "system") {
      setThemeSource(theme);
    }
  }, [theme]);
  return null;
}

/**
 * Forces a theme on the provider above without storing it, or clears the
 * force (`undefined`). Only story hosts provide it, so dark stories can turn
 * portalled UI dark too.
 */
export const ForceThemeContext = createContext<
  ((theme: "light" | "dark" | undefined) => void) | null
>(null);

/**
 * App-wide theme provider configured for Tailwind's `.dark` class variant.
 *
 * Defaults to system preference and stores explicit user selection under
 * an web-specific key so it does not collide with unrelated local apps
 * on the same host.
 *
 * @param children React tree that should inherit theme context.
 * @param forcedTheme A theme to show regardless of the stored one (never stored).
 * @returns React provider wrapping the app.
 */
export function ThemeProvider({
  children,
  forcedTheme,
}: {
  children: ReactNode;
  forcedTheme?: "light" | "dark";
}) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      storageKey="web-theme"
      forcedTheme={forcedTheme}
    >
      <NativeThemeSync />
      {children}
    </NextThemesProvider>
  );
}
