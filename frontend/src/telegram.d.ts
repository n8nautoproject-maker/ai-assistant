interface TelegramWebApp {
  initData: string;
  ready: () => void;
  expand: () => void;
  colorScheme: 'light' | 'dark';
  themeParams: Record<string, string>;
  // Both optional - only present on Bot API versions new enough to support
  // them. Without setting these explicitly, Telegram's native header/body
  // area above our content defaults to its own theme color instead of
  // matching --bg, which reads as an unstyled dead strip above "Задачи".
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  // Telegram's own recommended way to open an external URL from inside a
  // Mini App - a plain window.open() call there isn't reliably treated as
  // originating from the user's tap (especially once it happens after an
  // await), so WebKit/the in-Telegram browser silently blocks it with no
  // error at all. Optional for the same reason as the color setters above.
  openLink?: (url: string) => void;
}

interface Window {
  Telegram?: {
    WebApp: TelegramWebApp;
  };
  // Set by LoginWidget.tsx before injecting Telegram's login widget script -
  // the widget calls this global by name (data-onauth="onTelegramAuth"),
  // there's no way to pass it a normal JS reference instead.
  onTelegramAuth?: (user: {
    id: number;
    first_name?: string;
    last_name?: string;
    username?: string;
    auth_date: number;
    hash: string;
  }) => void;
}
