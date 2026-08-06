/** Ссылка на бота: поменяйте username на реальный после регистрации в BotFather */
export const BOT_URL =
  process.env.NEXT_PUBLIC_BOT_URL || 'https://t.me/SystemAriseBot'

/** Публичный адрес лендинга — нужен для metadataBase, sitemap и OG-тегов. */
export const SITE_URL =
  process.env.NEXT_PUBLIC_SITE_URL || 'https://sololevelingbot.vercel.app'

/** Контакт поддержки, указанный в /privacy, /terms и /paysupport бота. */
export const SUPPORT_CONTACT =
  process.env.NEXT_PUBLIC_SUPPORT_CONTACT || '@SystemAriseSupport'

export const LEGAL_UPDATED = '6 августа 2026'

export const PRICES = {
  premium: 149,
  premiumDays: 30,
  revive: 49,
  freeze: 25,
  freeReports: 1,
  premiumReports: 3,
  freeCustomQuests: 3,
  premiumCustomQuests: 10,
} as const
