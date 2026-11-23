import { Component, type ComponentChildren } from "preact";
import { deviceLang, setLang, t } from "../i18n";
import type { Store } from "../model/storage";

/**
 * The app's error boundary (#846): when a view breaks, often on a saved plan the app cannot draw, the athlete gets a
 * short explanation and a button that deletes all data on the device and starts the app again, instead of a white
 * page with *Ta bort all data* out of reach.
 */
export class Broken extends Component<{ store: Store; reload: () => void; children?: ComponentChildren },
  { failed: boolean; deleting: boolean }> {
  state = { failed: false, deleting: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(e: unknown) {
    console.error(e);
    this.setState({ failed: true });
  }

  private remove = async () => {
    this.setState({ deleting: true });
    try {
      this.props.store.clear();
      await this.props.store.flush();
    } finally {
      this.props.reload();
    }
  };

  render() {
    if (!this.state.failed) return this.props.children;
    try {
      setLang(this.props.store.settings().lang ?? deviceLang());
    } catch {
      /* the settings are broken too: the language stays */
    }
    return (
      <div class="app">
        <main>
          <section class="view broken" role="alert">
            <h2>{t("ui.broken.title")}</h2>
            <p>{t("ui.broken.text")}</p>
            <button class="danger wide" disabled={this.state.deleting} onClick={() => {
              if (typeof confirm !== "function" || confirm(t("ui.about.delete_confirm"))) void this.remove();
            }}>{t("ui.broken.delete")}</button>
            <p class="muted small">{t("ui.broken.copy")}</p>
          </section>
        </main>
      </div>
    );
  }
}
