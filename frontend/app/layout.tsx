import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Backlog Beater",
  description: "Import your game library and decide what to finish next.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
