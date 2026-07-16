'use strict';

/**
 * fingerprint.js — Apply user-provided fingerprint overrides.
 *
 * Overwrites navigator, screen, and window properties based on input fingerprint.
 * Runs AFTER chrome-overlay, so it can override any default.
 */

function applyFingerprint(ctx, fingerprint, markNativeHandle) {
  if (!fingerprint || typeof fingerprint !== 'object') return;

  const { markNative } = markNativeHandle;
  const win = ctx;
  const nav = win.navigator;
  const screen = win.screen;

  // --- Navigator overrides ---
  const navFieldMap = {
    user_agent: 'userAgent',
    platform: 'platform',
    languages: 'languages',
    vendor: 'vendor',
    hardware_concurrency: 'hardwareConcurrency',
    device_memory: 'deviceMemory',
    max_touch_points: 'maxTouchPoints',
    connection_effective_type: null, // handled separately
  };

  if (nav) {
    const navProto = Object.getPrototypeOf(nav) || nav;
    for (const [fpKey, navKey] of Object.entries(navFieldMap)) {
      if (fpKey in fingerprint && navKey) {
        const value = fingerprint[fpKey];
        const getter = markNative(function() {
          if (this !== nav) throw new TypeError('Illegal invocation');
          return value;
        }, `get ${navKey}`);
        Object.defineProperty(navProto, navKey, {
          get: getter,
          enumerable: true, configurable: true,
        });
      }
    }

    // Sync appVersion when user_agent is overridden
    if (fingerprint.user_agent) {
      const ua = fingerprint.user_agent;
      const av = ua.indexOf('Mozilla/') === 0 ? ua.slice(8) : ua;
      Object.defineProperty(navProto, 'appVersion', {
        get: markNative(function() {
          if (this !== nav) throw new TypeError('Illegal invocation');
          return av;
        }, 'get appVersion'),
        enumerable: true, configurable: true,
      });
    }

    // Special: language derived from languages
    if (fingerprint.languages && Array.isArray(fingerprint.languages) && fingerprint.languages.length > 0) {
      const lang = fingerprint.languages[0];
      Object.defineProperty(navProto, 'language', {
        get: markNative(function() {
          if (this !== nav) throw new TypeError('Illegal invocation');
          return lang;
        }, 'get language'),
        enumerable: true, configurable: true,
      });
      // Chrome returns a NEW frozen array each access (languages === languages is false)
      const langSource = [...fingerprint.languages];
      const CtxArray = ctx.Array || Array;
      const CtxObject = ctx.Object || Object;
      Object.defineProperty(navProto, 'languages', {
        get: markNative(function() {
          if (this !== nav) throw new TypeError('Illegal invocation');
          // Must create array in vm context realm so instanceof Array works
          const arr = new CtxArray(langSource.length);
          for (let i = 0; i < langSource.length; i++) arr[i] = langSource[i];
          return CtxObject.freeze(arr);
        }, 'get languages'),
        enumerable: true, configurable: true,
      });
    }

    // Special: connection.effectiveType
    if (fingerprint.connection_effective_type && nav.connection) {
      const effType = fingerprint.connection_effective_type;
      Object.defineProperty(nav.connection, 'effectiveType', {
        get: markNative(function() { return effType; }, 'get effectiveType'),
        enumerable: true, configurable: true,
      });
    }
  }

  // --- Screen overrides ---
  const screenFieldMap = {
    screen_width: 'width',
    screen_height: 'height',
    screen_avail_width: 'availWidth',
    screen_avail_height: 'availHeight',
    screen_color_depth: 'colorDepth',
    screen_pixel_depth: 'pixelDepth',
  };

  if (screen) {
    const screenProto = Object.getPrototypeOf(screen) || screen;
    for (const [fpKey, screenKey] of Object.entries(screenFieldMap)) {
      if (fpKey in fingerprint) {
        const value = fingerprint[fpKey];
        Object.defineProperty(screenProto, screenKey, {
          get: markNative(function() {
            if (this !== screen) throw new TypeError('Illegal invocation');
            return value;
          }, `get ${screenKey}`),
          enumerable: true, configurable: true,
        });
      }
    }
  }

  // --- Window dimension overrides (getter only, no setter — matches Chrome) ---
  const winFieldMap = {
    inner_width: 'innerWidth',
    inner_height: 'innerHeight',
    outer_width: 'outerWidth',
    outer_height: 'outerHeight',
    device_pixel_ratio: 'devicePixelRatio',
  };

  for (const [fpKey, winKey] of Object.entries(winFieldMap)) {
    if (fpKey in fingerprint) {
      const value = fingerprint[fpKey];
      Object.defineProperty(win, winKey, {
        get: markNative(function() { return value; }, `get ${winKey}`),
        enumerable: true, configurable: true,
      });
    }
  }

  // --- Timezone override ---
  if (fingerprint.timezone) {
    try {
      const tz = fingerprint.timezone;
      const CtxIntl = ctx.Intl || Intl;
      const origResolvedOptions = CtxIntl.DateTimeFormat.prototype.resolvedOptions;
      CtxIntl.DateTimeFormat.prototype.resolvedOptions = markNative(function resolvedOptions() {
        const opts = origResolvedOptions.call(this);
        opts.timeZone = tz;
        return opts;
      }, 'resolvedOptions');

      // Patch Date.prototype.getTimezoneOffset — DST-aware via Intl
      // Real Chrome: offset changes based on whether the date is in DST or not.
      // Use Intl.DateTimeFormat to compute the correct offset for any given date.
      const CtxDate = ctx.Date;
      CtxDate.prototype.getTimezoneOffset = markNative(function getTimezoneOffset() {
        // Compute real offset for THIS date in the target timezone
        try {
          const dtf = new CtxIntl.DateTimeFormat('en-US', {
            timeZone: tz,
            year: 'numeric', month: 'numeric', day: 'numeric',
            hour: 'numeric', minute: 'numeric', second: 'numeric',
            hour12: false,
          });
          const parts = dtf.formatToParts(this);
          const get = (type) => parseInt((parts.find(p => p.type === type) || {}).value) || 0;
          // Reconstruct local time as UTC to compute offset
          const localAsUTC = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour') % 24, get('minute'), get('second'));
          // offset = (UTC_timestamp - local_as_UTC) / 60000
          return Math.round((this.getTime() - localAsUTC) / 60000);
        } catch (e) {
          // Fallback to static map if Intl fails
          const staticMap = { 'Asia/Shanghai': -480, 'Asia/Tokyo': -540, 'America/New_York': 300, 'America/Los_Angeles': 480, 'Europe/London': 0, 'Europe/Paris': -60 };
          return staticMap[tz] !== undefined ? staticMap[tz] : 0;
        }
      }, 'getTimezoneOffset');
    } catch (e) {}
  }
}

module.exports = { applyFingerprint };
