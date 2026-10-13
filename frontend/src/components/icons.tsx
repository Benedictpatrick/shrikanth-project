/** Small 16px stroke icons in the spirit of GitHub's Octicons (original paths). */
const PATHS: Record<string, React.ReactNode> = {
  logo: <path d="M3 2.5h4.5v11H3zM8.5 2.5H13v4.8H8.5zM8.5 8.7H13v4.8H8.5z" fill="currentColor" stroke="none" />,
  overview: <path d="M2 3.5h4.5A1.5 1.5 0 0 1 8 5v8a1.5 1.5 0 0 0-1.5-1.5H2zM14 3.5H9.5A1.5 1.5 0 0 0 8 5v8a1.5 1.5 0 0 1 1.5-1.5H14z" />,
  graph: <><circle cx="3.5" cy="8" r="1.7" /><circle cx="12.5" cy="3.5" r="1.7" /><circle cx="12.5" cy="12.5" r="1.7" /><path d="M5 7.2 11 4.3M5 8.8 11 11.7" /></>,
  file: <path d="M3.75 1.75h5L12.25 5.5v8.75h-8.5zM8.75 1.75V5.5h3.5" />,
  shield: <path d="M8 1.5 13.5 3.5v4.2c0 3-2.3 5.4-5.5 6.8C4.8 13.1 2.5 10.7 2.5 7.7V3.5z" />,
  plan: <path d="M2.5 4.5l1.2 1.2 2-2.2M2.5 10.5l1.2 1.2 2-2.2M8 5h5.5M8 11h5.5" />,
  migrate: <><circle cx="4" cy="3.5" r="1.6" /><circle cx="4" cy="12.5" r="1.6" /><circle cx="12" cy="12.5" r="1.6" /><path d="M4 5.1v5.8M12 10.9V6.7a2 2 0 0 0-2-2H8.5M10 3 8.5 4.7 10 6.4" /></>,
  package: <path d="M8 1.5 13.5 4.5v7L8 14.5 2.5 11.5v-7zM2.5 4.5 8 7.5l5.5-3M8 7.5v7" />,
  gear: <><circle cx="8" cy="8" r="2.3" /><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4" /></>,
  check: <><circle cx="8" cy="8" r="6.2" /><path d="M5.2 8.2 7.1 10 10.8 6.2" /></>,
  progress: <><circle cx="8" cy="8" r="6.2" /><circle cx="8" cy="8" r="2" fill="currentColor" stroke="none" /></>,
  pending: <circle cx="8" cy="8" r="6.2" />,
  x: <><circle cx="8" cy="8" r="6.2" /><path d="M5.7 5.7l4.6 4.6M10.3 5.7l-4.6 4.6" /></>,
  alert: <path d="M8 2 14.5 13.5h-13zM8 6.5v3.2M8 11.4v.1" />,
  merge: <><circle cx="4" cy="3.5" r="1.6" /><circle cx="4" cy="12.5" r="1.6" /><circle cx="12" cy="8.2" r="1.6" /><path d="M4 5.1v5.8M5.6 3.6C9 3.6 12 5 12 6.6" /></>,
  plus: <path d="M8 3v10M3 8h10" />,
  trash: <path d="M3 4.5h10M6.2 4.5V3h3.6v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5" />,
  key: <><circle cx="5" cy="10" r="2.6" /><path d="M7 8.2 13 2.5M11 4.5l1.5 1.5M9.3 6.2l1.2 1.2" /></>,
  download: <path d="M8 2v8M4.8 7 8 10.2 11.2 7M2.5 13h11" />,
}

export function Icon({ name, size = 16 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={{ flex: 'none' }}>
      {PATHS[name] ?? PATHS.pending}
    </svg>
  )
}
