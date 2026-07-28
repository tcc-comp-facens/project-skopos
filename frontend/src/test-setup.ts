import '@testing-library/jest-dom';

// jsdom não implementa scrollIntoView — usado pelo auto-scroll do chat.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
