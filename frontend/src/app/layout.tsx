import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/lib/theme";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Forex Bot Dashboard",
  description: "Advanced Agentic Forex Trading Bot Dashboard",
};

// Aplica a classe de tema antes do primeiro paint. Sem isso, a página nasce
// no tema default do ThemeProvider (dark) e "pisca" para light quando o
// usuário tinha escolhido light — o clássico flash of unstyled/wrong theme.
const ANTI_FOUC_SCRIPT = `
(function () {
  try {
    var stored = window.localStorage.getItem("forex-bot-theme");
    var theme = stored === "light" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.setAttribute("data-theme", theme);
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: ANTI_FOUC_SCRIPT }} />
      </head>
      <body className={`${inter.variable} antialiased`} suppressHydrationWarning>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
