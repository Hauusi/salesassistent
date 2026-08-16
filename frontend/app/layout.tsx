import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mail-Assistent",
  description: "Sales-Assistent: Mail-Modul (MVP)",
};

const NAV_ITEMS = [
  { href: "/drafts", label: "Freigaben" },
  { href: "/inbox", label: "Posteingang" },
  { href: "/knowledge", label: "Wissensbasis" },
  { href: "/cases", label: "Cases" },
  { href: "/connect", label: "Postfach verbinden" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="de">
      <body>
        <div className="layout">
          <aside className="sidebar">
            <h1>Mail-Assistent</h1>
            <nav>
              {NAV_ITEMS.map((item) => (
                <a key={item.href} href={item.href}>
                  {item.label}
                </a>
              ))}
            </nav>
          </aside>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
