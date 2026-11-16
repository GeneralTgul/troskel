import type { CapacitorConfig } from '@capacitor/cli';

// The app-store build of Tröskel. The same web app (dist/) runs as a PWA on GitHub Pages.
// No server URL: the app loads only its own files, and nothing leaves the device.
const config: CapacitorConfig = {
  appId: 'io.github.generaltgul.troskel',
  appName: 'Tröskel',
  webDir: 'dist',
  backgroundColor: '#141A21',
  android: {
    // No http:// content and no WebView debugging in a release build.
    allowMixedContent: false,
  },
  ios: {
    contentInset: 'automatic',
  },
};

export default config;
