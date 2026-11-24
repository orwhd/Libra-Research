/**
 * Safely validates messages from `<iframe sandbox="allow-scripts">` opaque contexts
 * per Modern Web Guidance `validate-origins`:
 * - Uses `Origin.from(event)` pinning when the `Origin` API is supported so two distinct
 *   `"null"` sandboxed origins cannot spoof each other.
 * - Falls back strictly to verifying `event.source === expectedWindow` when `Origin` is absent.
 */
interface OriginCtor {
  new (): OriginInstance;
  from(candidate: unknown): OriginInstance;
}

interface OriginInstance {
  opaque: boolean;
  isSameOrigin(other: OriginInstance): boolean;
}

export class SandboxedIframeOriginPin {
  private pinnedOrigin: OriginInstance | null = null;
  private isPinned = false;

  public reset(): void {
    this.pinnedOrigin = null;
    this.isPinned = false;
  }

  public verify(event: MessageEvent, expectedWindow: Window | null): boolean {
    if (!expectedWindow || event.source !== expectedWindow) {
      return false;
    }
    const OriginApi = (globalThis as unknown as { Origin?: OriginCtor }).Origin;
    if (typeof OriginApi !== 'undefined' && typeof OriginApi.from === 'function') {
      try {
        const senderOrigin = OriginApi.from(event);
        if (!this.isPinned) {
          this.pinnedOrigin = senderOrigin;
          this.isPinned = true;
        }
        return !!this.pinnedOrigin && senderOrigin.isSameOrigin(this.pinnedOrigin);
      } catch {
        return false;
      }
    }
    // Fallback where `Origin` API is unavailable: `event.source === expectedWindow` already verified above,
    // and we do NOT rely on `event.origin === "null"` string comparison alone.
    return true;
  }
}
