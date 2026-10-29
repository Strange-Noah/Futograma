export const expressions = {
  "bodyText": "document.body?.innerText||\"\"",
  "profileReady": "Boolean(document.querySelector(\"h1\")?.innerText.trim()) && /Date of birth|Born|Height|Position/i.test(document.body.innerText)",
  "rosterReady": "Array.from(document.querySelectorAll(\"a[href]\")).filter(a=>a.getAttribute(\"href\").includes(\"/fixtures/men/players/\")).length>10",
  "html": "document.documentElement.outerHTML",
  "url": "location.href",
  "links": "Array.from(document.querySelectorAll(\"a[href]\"),a=>({href:a.href,text:a.innerText})).filter(a=>a.href.includes(\"/fixtures/men/players/\"))"
};
