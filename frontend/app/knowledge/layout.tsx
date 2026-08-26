"use client";

import { usePathname } from "next/navigation";

const TABS = [
  { href: "/knowledge", label: "Mails" },
  { href: "/knowledge/products", label: "Produkte" },
  { href: "/knowledge/product-suggestions", label: "Produktvorschläge" },
];

export default function KnowledgeLayout({ children }: LayoutProps<"/knowledge">) {
  const pathname = usePathname();

  return (
    <div>
      <div className="row" style={{ gap: 4, marginBottom: 20, borderBottom: "1px solid var(--border)" }}>
        {TABS.map((tab) => {
          const active = pathname === tab.href;
          return (
            <a
              key={tab.href}
              href={tab.href}
              style={{
                padding: "8px 14px",
                fontSize: 14,
                fontWeight: 600,
                color: active ? "var(--primary)" : "var(--text-muted)",
                borderBottom: active ? "2px solid var(--primary)" : "2px solid transparent",
                marginBottom: -1,
              }}
            >
              {tab.label}
            </a>
          );
        })}
      </div>
      {children}
    </div>
  );
}
