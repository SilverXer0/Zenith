import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Zenith — Your day, in focus",
  description: "A calm, local-first personal manager.",
};

export const viewport: Viewport = { themeColor: "#171916" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
