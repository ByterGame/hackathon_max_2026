import type { ReactNode } from "react";

export type IconName =
  | "home" | "building" | "bell" | "plus" | "back" | "chevron" | "list"
  | "people" | "user" | "pin" | "clock" | "chat" | "check" | "filter"
  | "send" | "search" | "paperclip" | "close" | "shield" | "edit"
  | "arrow" | "info" | "sparkles" | "key" | "file" | "video" | "image" | "pdf" | "download";

const paths: Record<IconName, ReactNode> = {
  home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z" /></>,
  building: <><rect x="5" y="2" width="14" height="20" rx="2" /><path d="M9 6h1m4 0h1M9 10h1m4 0h1M9 14h1m4 0h1M10 22v-4h4v4" /></>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4" /></>,
  plus: <><path d="M12 5v14M5 12h14" /></>,
  back: <><path d="m15 18-6-6 6-6" /></>,
  chevron: <><path d="m9 18 6-6-6-6" /></>,
  list: <><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></>,
  people: <><circle cx="9" cy="8" r="3" /><path d="M3 20v-2a6 6 0 0 1 12 0v2H3ZM17 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 5h-4" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  pin: <><path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z" /><circle cx="12" cy="10" r="2.5" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  chat: <><path d="M20 16a3 3 0 0 1-3 3H8l-5 3V6a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3z" /></>,
  check: <><path d="m4 12 5 5L20 6" /></>,
  filter: <><path d="M4 7h16M7 12h10m-7 5h4" /><circle cx="9" cy="7" r="2" /><circle cx="15" cy="12" r="2" /></>,
  send: <><path d="m22 2-7 20-4-9-9-4zM11 13 22 2" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
  paperclip: <><path d="m8 12 6-6a4 4 0 0 1 6 6l-8 8a6 6 0 0 1-9-9l9-9" /></>,
  close: <><path d="M5 5 19 19M19 5 5 19" /></>,
  shield: <><path d="M12 2 4 5v6c0 5 3 8 8 11 5-3 8-6 8-11V5z" /><path d="m9 12 2 2 4-4" /></>,
  edit: <><path d="m4 20 4-.8L20 7a2 2 0 0 0-3-3L5 16z" /></>,
  arrow: <><path d="M4 12h16m-6-6 6 6-6 6" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
  sparkles: <><path d="m12 2 2.2 6.8L21 11l-6.8 2.2L12 20l-2.2-6.8L3 11l6.8-2.2L12 2ZM19 18l.8 2.2L22 21l-2.2.8L19 24l-.8-2.2L16 21l2.2-.8z" /></>,
  key: <><circle cx="8" cy="9" r="5" /><path d="M12 12 21 21m-4-4 2-2m0 4 2-2" /></>,
  file: <><path d="M6 2h8l5 5v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" /><path d="M14 2v6h5M8 13h8M8 17h6" /></>,
  video: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m10 9 5 3-5 3V9Z" /></>,
  image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8" cy="8" r="1.5" /><path d="m3 17 5-5 3 3 3-4 7 7" /></>,
  pdf: <><path d="M6 2h8l5 5v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" /><path d="M14 2v6h5M8 17h8M8 13h5" /></>,
  download: <><path d="M12 3v12m-4-4 4 4 4-4M4 18v3h16v-3" /></>,
};

export function Icon({ name, size = 22, className = "" }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}
