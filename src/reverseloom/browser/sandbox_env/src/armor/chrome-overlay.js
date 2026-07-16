'use strict';

/**
 * chrome-overlay.js — Chrome-specific browser patches.
 *
 * Applied on TOP of jsdom's window. Overwrites values that jsdom gets wrong
 * and adds Chrome-only objects (window.chrome, navigator tweaks, screen values).
 *
 * Philosophy: jsdom provides the DOM structure, we provide the Chrome identity.
 */

// Default values matching Chrome 120+ on Windows
const NAVIGATOR_DEFAULTS = {
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  platform: 'Win32',
  vendor: 'Google Inc.',
  vendorSub: '',
  product: 'Gecko',
  productSub: '20030107',
  appName: 'Netscape',
  appCodeName: 'Mozilla',
  language: 'zh-CN',
  languages: null, // handled specially — Chrome returns new frozen array each access
  onLine: true,
  cookieEnabled: true,
  maxTouchPoints: 0,
  hardwareConcurrency: 8,
  deviceMemory: 8,
  pdfViewerEnabled: true,
  doNotTrack: null,
};

const SCREEN_DEFAULTS = {
  width: 1920,
  height: 1080,
  availWidth: 1920,
  availHeight: 1040,
  availLeft: 0,
  availTop: 0,
  colorDepth: 24,
  pixelDepth: 24,
};

function installChromeOverlay(ctx, markNativeHandle) {
  const { markNative } = markNativeHandle;
  const win = ctx;
  const ctxObjectProto = (ctx.Object || Object).prototype;

  // --- Navigator property overrides ---
  // Chrome defines these on Navigator.prototype (not on the instance).
  // Defining on prototype ensures navigator.hasOwnProperty('userAgent') === false.
  // Getters throw "Illegal invocation" if called with wrong `this` (matches Chrome behavior).
  const nav = win.navigator;
  if (nav) {
    const navProto = Object.getPrototypeOf(nav);

    // Fix: Chrome has `navigator instanceof EventTarget === true`
    // Instead of modifying prototype chain (breaks jsdom internals), patch Symbol.hasInstance
    if (win.EventTarget && !nav.addEventListener) {
      // If navigator doesn't have addEventListener, add minimal EventTarget interface
      nav.addEventListener = markNative(function addEventListener() {}, 'addEventListener');
      nav.removeEventListener = markNative(function removeEventListener() {}, 'removeEventListener');
      nav.dispatchEvent = markNative(function dispatchEvent() { return true; }, 'dispatchEvent');
    }
    if (win.EventTarget) {
      try {
        const origHasInstance = win.EventTarget[Symbol.hasInstance];
        const hasInstanceFn = function(instance) {
          if (instance === nav) return true;
          if (origHasInstance) return origHasInstance.call(this, instance);
          return instance instanceof this;
        };
        markNative(hasInstanceFn, '[Symbol.hasInstance]');
        Object.defineProperty(win.EventTarget, Symbol.hasInstance, {
          value: hasInstanceFn,
          writable: true, configurable: true,
        });
      } catch (e) {}
    }

    // Use a hidden symbol for Illegal invocation check.
    // Symbol is invisible to target scripts AND works through Proxy transparently:
    // when monitor wraps navigator in a Proxy, `this[_navMarker]` still resolves
    // because the Proxy's get trap forwards to the real nav object.
    const _navMarker = Symbol('__nav__');
    Object.defineProperty(nav, _navMarker, { value: true, configurable: true });
    const target = navProto || nav;  // fallback to instance if no prototype
    for (const [key, value] of Object.entries(NAVIGATOR_DEFAULTS)) {
      if (value === null) continue; // skip specially-handled fields (e.g. languages)
      const getter = function() {
        if (!this || !this[_navMarker]) {
          throw new TypeError('Illegal invocation');
        }
        return value;
      };
      Object.defineProperty(target, key, {
        get: getter,
        enumerable: true,
        configurable: true,
      });
      markNative(getter, `get ${key}`);
    }

    // navigator.languages — Chrome returns a NEW frozen array on every access
    // (navigator.languages === navigator.languages) is false in real Chrome
    const defaultLangs = ['zh-CN', 'zh', 'en'];
    const CtxArray = ctx.Array || Array;
    const CtxObject = ctx.Object || Object;
    Object.defineProperty(target, 'languages', {
      get: markNative(function() {
        if (!this || !this[_navMarker]) {
          throw new TypeError('Illegal invocation');
        }
        // Must create array in vm context realm so instanceof Array works
        const arr = new CtxArray(defaultLangs.length);
        for (let i = 0; i < defaultLangs.length; i++) arr[i] = defaultLangs[i];
        return CtxObject.freeze(arr);
      }, 'get languages'),
      enumerable: true,
      configurable: true,
    });

    // navigator.doNotTrack — Chrome 120+: getter returning null (property exists, value is null)
    Object.defineProperty(target, 'doNotTrack', {
      get: markNative(function() {
        if (!this || !this[_navMarker]) throw new TypeError('Illegal invocation');
        return null;
      }, 'get doNotTrack'),
      enumerable: true,
      configurable: true,
    });

    // navigator.connection (NetworkInformation API)
    if (!nav.connection) {
      // NetworkInformation constructor throws "Illegal constructor" (Chrome behavior)
      const NetworkInformation = markNative(function NetworkInformation() {
        throw new TypeError('Illegal constructor');
      }, 'NetworkInformation');
      NetworkInformation.prototype = Object.create(win.EventTarget ? win.EventTarget.prototype : win.Object.prototype);
      Object.defineProperty(NetworkInformation.prototype, Symbol.toStringTag, { value: 'NetworkInformation', configurable: true });
      Object.defineProperty(NetworkInformation.prototype, 'constructor', { value: NetworkInformation, writable: true, configurable: true });
      win.NetworkInformation = NetworkInformation;

      const connection = { __proto__: NetworkInformation.prototype };
      let _onchange = null;
      Object.defineProperties(connection, {
        effectiveType: { get: markNative(function() { if (this !== connection) throw new TypeError('Illegal invocation'); return '4g'; }, 'get effectiveType'), enumerable: true, configurable: true },
        rtt: { get: markNative(function() { if (this !== connection) throw new TypeError('Illegal invocation'); return 50; }, 'get rtt'), enumerable: true, configurable: true },
        downlink: { get: markNative(function() { if (this !== connection) throw new TypeError('Illegal invocation'); return 10; }, 'get downlink'), enumerable: true, configurable: true },
        saveData: { get: markNative(function() { if (this !== connection) throw new TypeError('Illegal invocation'); return false; }, 'get saveData'), enumerable: true, configurable: true },
        onchange: { get: markNative(function() { return _onchange; }, 'get onchange'), set: markNative(function(v) { _onchange = v; }, 'set onchange'), enumerable: true, configurable: true },
      });
      Object.defineProperty(navProto || nav, 'connection', {
        get: markNative(function() { return connection; }, 'get connection'),
        enumerable: true,
        configurable: true,
      });
    }

    // navigator.webdriver — Chrome 120+ has a getter on Navigator.prototype that returns false.
    // Automated Chrome (ChromeDriver) returns true. Normal user = false.
    // CRITICAL: must NOT delete it — 'webdriver' in navigator must return true.
    Object.defineProperty(navProto || nav, 'webdriver', {
      get: markNative(function() {
        if (!this || !this[_navMarker]) throw new TypeError('Illegal invocation');
        return false;
      }, 'get webdriver'),
      enumerable: true,
      configurable: true,
    });

    // navigator.plugins / mimeTypes — handled by browser-apis.js (proper PluginArray)
  }

  // --- Screen property overrides ---
  // Chrome defines these on Screen.prototype (not on the instance).
  const screen = win.screen;
  if (screen) {
    const screenProto = Object.getPrototypeOf(screen);
    const screenTarget = screenProto || screen;
    // Symbol marker for Illegal invocation check (Proxy-transparent, same approach as navigator)
    const _screenMarker = Symbol('__screen__');
    Object.defineProperty(screen, _screenMarker, { value: true, configurable: true });
    for (const [key, value] of Object.entries(SCREEN_DEFAULTS)) {
      Object.defineProperty(screenTarget, key, {
        get: markNative(function() {
          if (!this || !this[_screenMarker]) throw new TypeError('Illegal invocation');
          return value;
        }, `get ${key}`),
        enumerable: true,
        configurable: true,
      });
    }
    // screen.orientation
    if (!screen.orientation) {
      const orientation = Object.create(win.EventTarget ? win.EventTarget.prototype : win.Object.prototype);
      Object.defineProperties(orientation, {
        type: { get: markNative(function() { return 'landscape-primary'; }, 'get type'), enumerable: true, configurable: true },
        angle: { get: markNative(function() { return 0; }, 'get angle'), enumerable: true, configurable: true },
      });
      Object.defineProperty(orientation, Symbol.toStringTag, { value: 'ScreenOrientation', configurable: true });
      orientation.lock = markNative(function lock() { return Promise.resolve(); }, 'lock');
      orientation.unlock = markNative(function unlock() {}, 'unlock');
      orientation.onchange = null;
      Object.defineProperty(screen, 'orientation', {
        get: markNative(function() { return orientation; }, 'get orientation'),
        enumerable: true, configurable: true,
      });
    }
  }

  // --- Window dimension properties (getter only, no setter — matches Chrome) ---
  const dimDefaults = {
    innerWidth: 1920, innerHeight: 1080,
    outerWidth: 1920, outerHeight: 1120,
    devicePixelRatio: 1,
    screenX: 0, screenY: 0, screenLeft: 0, screenTop: 0,
    pageXOffset: 0, pageYOffset: 0, scrollX: 0, scrollY: 0,
  };
  for (const [key, value] of Object.entries(dimDefaults)) {
    Object.defineProperty(win, key, {
      get: markNative(function() {
        if (this !== win) throw new TypeError('Illegal invocation');
        return value;
      }, `get ${key}`),
      enumerable: true,
      configurable: true,
    });
  }

  // --- window.chrome object ---
  // CRITICAL: Must use ctx realm's Object.prototype, not host realm literal
  const CtxObject = ctx.Object || Object;
  const ctxFreeze = CtxObject.freeze;
  const chrome = CtxObject.create(ctxObjectProto);
  chrome.app = CtxObject.create(ctxObjectProto);
  chrome.runtime = CtxObject.create(ctxObjectProto);
  // CRITICAL: chrome.csi was removed in Chrome 107, chrome.loadTimes in Chrome 117.
  // For Chrome 120+ (our target), these must NOT exist — their presence signals a fake/old Chrome.
  // Only chrome.app and chrome.runtime remain on normal web pages.

  // Helper: create frozen enum object in ctx realm
  function ctxFrozenEnum(entries) {
    const obj = CtxObject.create(ctxObjectProto);
    for (const k of Object.keys(entries)) obj[k] = entries[k];
    return ctxFreeze(obj);
  }

  // chrome.app — methods are non-enumerable in real Chrome
  Object.defineProperties(chrome.app, {
    isInstalled: { value: false, writable: true, enumerable: true, configurable: true },
    InstallState: { value: ctxFrozenEnum({ DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }), enumerable: true, configurable: true },
    RunningState: { value: ctxFrozenEnum({ CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }), enumerable: true, configurable: true },
    getDetails: { value: markNative(function getDetails() { return null; }, 'getDetails'), enumerable: false, configurable: true },
    getIsInstalled: { value: markNative(function getIsInstalled() { return false; }, 'getIsInstalled'), enumerable: false, configurable: true },
    runningState: { value: markNative(function runningState() { return 'cannot_run'; }, 'runningState'), enumerable: false, configurable: true },
    installState: { value: markNative(function installState() { return 'not_installed'; }, 'installState'), enumerable: false, configurable: true },
  });

  // chrome.runtime — methods are non-enumerable in real Chrome
  Object.defineProperties(chrome.runtime, {
    id: { value: undefined, writable: true, enumerable: false, configurable: true },
    lastError: { value: undefined, writable: true, enumerable: false, configurable: true },
    OnInstalledReason: { value: ctxFrozenEnum({ CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' }), enumerable: true, configurable: true },
    OnRestartRequiredReason: { value: ctxFrozenEnum({ APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' }), enumerable: true, configurable: true },
    PlatformArch: { value: ctxFrozenEnum({ ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' }), enumerable: true, configurable: true },
    PlatformOs: { value: ctxFrozenEnum({ ANDROID: 'android', CROS: 'cros', FUCHSIA: 'fuchsia', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' }), enumerable: true, configurable: true },
    RequestUpdateCheckStatus: { value: ctxFrozenEnum({ NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }), enumerable: true, configurable: true },
    connect: { value: markNative(function connect() { return undefined; }, 'connect'), enumerable: false, configurable: true },
    sendMessage: { value: markNative(function sendMessage() { return undefined; }, 'sendMessage'), enumerable: false, configurable: true },
  });

  Object.defineProperty(win, 'chrome', {
    value: chrome,
    writable: true,
    enumerable: true,
    configurable: true,
  });
}

module.exports = { installChromeOverlay, NAVIGATOR_DEFAULTS, SCREEN_DEFAULTS };
