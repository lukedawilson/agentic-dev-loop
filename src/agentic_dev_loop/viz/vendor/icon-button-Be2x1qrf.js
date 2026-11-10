/**
* @license
*
* Copyright IBM Corp. 2019, 2026
*
* This source code is licensed under the Apache-2.0 license found in the
* LICENSE file in the root directory of this source tree.
*
* Also refer to the following links for third-party dependencies:
* https://www.npmjs.com/package/@floating-ui/core
* https://www.npmjs.com/package/@floating-ui/dom
* https://www.npmjs.com/package/@floating-ui/utils
* https://www.npmjs.com/package/@ibm/telemetry-js
* https://www.npmjs.com/package/@lit-labs/signals
* https://www.npmjs.com/package/@lit/context
* https://www.npmjs.com/package/@lit/reactive-element
* https://www.npmjs.com/package/lit
* https://www.npmjs.com/package/lit-element
* https://www.npmjs.com/package/lit-html
* https://www.npmjs.com/package/lodash-es
* https://www.npmjs.com/package/tslib
*/
import{c as e,d as t,f as n,t as r}from"./decorate-1Ivfrp93.js";import{n as i}from"./decorators-CNcdZSLL.js";import"./settings-DlHDxgf_.js";import{t as a}from"./carbon-element-Bi7pQHAl.js";import{n as o,t as s}from"./button-rSg_K8yf.js";import"./button.min.js";import{t as c}from"./tooltip-DYtXwn-o.js";import"./tooltip.min.js";var l=n([`@keyframes cds--hide-feedback{0%{opacity:1;visibility:inherit}to{opacity:0;visibility:hidden}}@keyframes cds--show-feedback{0%{opacity:0;visibility:hidden}to{opacity:1;visibility:inherit}}@keyframes cds--skeleton{0%{opacity:.3;transform:scaleX(0);transform-origin:left}20%{opacity:1;transform:scaleX(1);transform-origin:left}28%{transform:scaleX(1);transform-origin:right}51%{transform:scaleX(0);transform-origin:right}58%{transform:scaleX(0);transform-origin:right}82%{transform:scaleX(1);transform-origin:right}83%{transform:scaleX(1);transform-origin:left}96%{transform:scaleX(0);transform-origin:left}to{opacity:.3;transform:scaleX(0);transform-origin:left}}.cds--assistive-text,.cds--visually-hidden{position:absolute;overflow:hidden;padding:0;border:0;margin:-1px;block-size:1px;clip:rect(0,0,0,0);inline-size:1px;visibility:inherit;white-space:nowrap}:host(cds-icon-button){--cds-layout-size-height-xs:1.5rem;--cds-layout-size-height-sm:2rem;--cds-layout-size-height-md:2.5rem;--cds-layout-size-height-lg:3rem;--cds-layout-size-height-xl:4rem;--cds-layout-size-height-2xl:5rem;--cds-layout-size-height-min:0px;--cds-layout-size-height-max:999999999px;--cds-layout-density-padding-inline-condensed:0.5rem;--cds-layout-density-padding-inline-normal:1rem;--cds-layout-density-padding-inline-min:0px;--cds-layout-density-padding-inline-max:999999999px}:host(cds-icon-button[kind=ghost]:not([disabled])) ::slotted([slot=icon]){color:var(--cds-icon-primary,#161616)}:host(cds-icon-button[kind=ghost][disabled]) ::slotted([slot=icon]){color:var(--cds-icon-disabled,rgba(22,22,22,.25))}`]);let u=class extends s{constructor(...e){super(...e),this.align=`top`,this.autoalign=!1,this.closeOnActivation=!0,this.defaultOpen=!1,this.enterDelayMs=100,this.leaveDelayMs=100,this.size=`lg`}connectedCallback(){super.connectedCallback(),t(this.renderRoot,[c,o,l])}updated(e){if(super.updated?.(e),e){this.shadowRoot?.querySelector(`cds-tooltip`)?.shadowRoot?.querySelector(`.cds--tooltip`)?.classList.add(`cds--icon-tooltip`);let e=this.querySelector(`[slot=tooltip-content]`)?.textContent;this.shadowRoot?.querySelector(`cds-tooltip`)?.querySelector(`button`)?.setAttribute(`aria-label`,String(e))}}_renderTooltipContent(){return e`
      <cds-tooltip-content ?hidden=${this.disabled}>
        <slot name="tooltip-content"></slot>
      </cds-tooltip-content>
    `}render(){let{align:t,autoalign:n,closeOnActivation:r,defaultOpen:i,enterDelayMs:a,leaveDelayMs:o}=this;return e`
      <cds-tooltip
        ?autoalign=${n}
        align=${t}
        ?defaultOpen=${i}
        ?closeOnActivation=${r}
        enter-delay-ms=${a}
        leave-delay-ms=${o}
        .dropShadow=${!1}>
        ${super.render()} ${this._renderTooltipContent()}
      </cds-tooltip>
    `}};r([i({reflect:!0,type:String})],u.prototype,`align`,void 0),r([i({type:Boolean,reflect:!0})],u.prototype,`autoalign`,void 0),r([i({attribute:`close-on-activation`,reflect:!0,type:Boolean})],u.prototype,`closeOnActivation`,void 0),r([i({reflect:!0,type:Boolean})],u.prototype,`defaultOpen`,void 0),r([i({attribute:`enter-delay-ms`,type:Number})],u.prototype,`enterDelayMs`,void 0),r([i({attribute:`leave-delay-ms`,type:Number})],u.prototype,`leaveDelayMs`,void 0),r([i({reflect:!0})],u.prototype,`size`,void 0),u=r([a(`cds-icon-button`)],u);var d=u;export{l as n,d as t};