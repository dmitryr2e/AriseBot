import { BOT_URL } from '@/lib/site'

export function SiteHeader() {
  return (
    <header className="flex items-center justify-between px-6 py-5 md:px-10">
      <span className="font-mono text-sm font-bold tracking-widest text-foreground uppercase">
        ARISE
      </span>
      <div className="flex items-center gap-4">
        <span className="hidden items-center gap-2 font-mono text-xs tracking-widest text-muted-foreground uppercase sm:flex">
          <span className="sys-pulse size-1.5 rounded-full bg-primary" aria-hidden="true" />
          {'ARISE.ONLINE // PLAYER.READY'}
        </span>
        <a href={BOT_URL} target="_blank" rel="noopener noreferrer" className="rounded-md border border-primary/40 px-4 py-1.5 font-mono text-xs tracking-wider text-primary uppercase transition-colors hover:bg-primary hover:text-primary-foreground">
          Открыть бота
        </a>
      </div>
    </header>
  )
}
