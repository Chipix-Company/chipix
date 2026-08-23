function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildPartialTagPattern(tag) {
  let pattern = escapeRegex(tag);
  for (let len = tag.length - 1; len > 0; len--) {
    pattern += "|" + escapeRegex(tag.substring(0, len)) + "$";
  }
  return new RegExp(pattern, "g");
}

function parseTagAttributes(openTagMatch) {
  const attrRegex = /<\w+([^>]*)>/;
  const attrMatch = attrRegex.exec(openTagMatch);
  if (!attrMatch || !attrMatch[1]) return {};

  const attrs = {};
  const kvRegex = /(\w+)\s*=\s*"([^"]*)"/g;
  let kv;
  while ((kv = kvRegex.exec(attrMatch[1])) !== null) {
    attrs[kv[1]] = kv[2];
  }
  return attrs;
}

export function findOpenTag(buffer, codeTag = "<GENERATEDCODE>", commentTag = null) {
  const tags = [];
  if (codeTag) tags.push({ tag: codeTag, type: "code" });
  if (commentTag) tags.push({ tag: commentTag, type: "comment" });

  if (tags.length < 1) return false;

  const pattern = tags.map((t) => escapeRegex(t.tag)).join("|");
  const reg = new RegExp(pattern, "g");
  const match = reg.exec(buffer);

  if (match && match[0]) {
    for (const t of tags) {
      if (match[0] === t.tag) return t.type;
    }
  }
  return false;
}

export function processTagContent(buffer, openTag, closeTag) {
  let buf = buffer;
  if (buf.startsWith(openTag)) {
    buf = buf.substring(openTag.length);
  }

  const closePattern = buildPartialTagPattern(closeTag);
  const match = closePattern.exec(buf);

  if (!match) {
    return { content: buf, remainingBuffer: "", isComplete: false };
  }

  const content = buf.substring(0, match.index);
  const remainingBuffer = buf.substring(match.index);
  const isComplete = match[0] === closeTag;

  return {
    content,
    remainingBuffer: isComplete ? remainingBuffer.substring(closeTag.length) : remainingBuffer,
    isComplete,
  };
}

let blockIdCounter = 0;

export class CodeBlockExtractor {
  constructor(options = {}) {
    this.codeTag = options.codeTag || "<GENERATEDCODE>";
    this.closeCodeTag = options.closeCodeTag || "</GENERATEDCODE>";
    this.commentTag = options.commentTag || null;
    this.closeCommentTag = options.closeCommentTag || null;

    this._buffer = "";
    this._inTag = false;
    this._tagType = null;
    this._openTag = null;
    this._closeTag = null;
    this._currentCode = "";
    this._currentLang = null;
    this._currentId = null;
    this._textAccum = "";

    this._textParts = [];
    this._codeBlocks = [];
  }

  feed(chunk) {
    this._buffer += chunk;
    this._textParts = [];
    this._codeBlocks = [];

    let safety = 0;
    while (safety++ < 1000) {
      if (!this._inTag) {
        const tagType = findOpenTag(this._buffer, this.codeTag, this.commentTag);
        if (!tagType) {
          this._textAccum += this._buffer;
          this._buffer = "";
          break;
        }

        this._inTag = true;
        this._tagType = tagType;

        if (tagType === "code") {
          this._openTag = this.codeTag;
          this._closeTag = this.closeCodeTag;
        } else {
          this._openTag = this.commentTag;
          this._closeTag = this.closeCommentTag;
        }

        const openIdx = this._buffer.indexOf(this._openTag);
        const before = this._buffer.substring(0, openIdx);
        if (before) {
          this._textAccum += before;
        }

        const attrs = parseTagAttributes(this._buffer.substring(openIdx));
        this._currentLang = attrs.lang || null;
        this._currentId = ++blockIdCounter;
        this._currentCode = "";

        this._buffer = this._buffer.substring(openIdx);
      }

      if (this._inTag) {
        const result = processTagContent(this._buffer, this._openTag, this._closeTag);
        this._currentCode += result.content;

        if (result.isComplete) {
          if (this._textAccum) {
            this._textParts.push(this._textAccum);
            this._textAccum = "";
          }

          if (this._tagType === "code") {
            this._codeBlocks.push({
              id: this._currentId,
              lang: this._currentLang,
              code: this._currentCode,
            });
          } else {
            this._textAccum += this._currentCode;
          }

          this._inTag = false;
          this._tagType = null;
          this._openTag = null;
          this._closeTag = null;
          this._currentCode = "";
          this._currentLang = null;
          this._currentId = null;
          this._buffer = result.remainingBuffer;

          if (!this._buffer) break;
        } else {
          this._buffer = result.remainingBuffer;
          break;
        }
      }
    }

    return { textParts: this._textParts, codeBlocks: this._codeBlocks };
  }

  flush() {
    const remaining = this._buffer;
    this._buffer = "";

    if (this._inTag && this._currentCode) {
      if (this._textAccum) {
        this._textParts.push(this._textAccum);
        this._textAccum = "";
      }
      if (this._tagType === "code") {
        this._codeBlocks.push({
          id: this._currentId,
          lang: this._currentLang,
          code: this._currentCode,
        });
      } else {
        this._textAccum += this._currentCode;
      }
    } else if (this._textAccum) {
      this._textParts.push(this._textAccum);
      this._textAccum = "";
    }

    this._inTag = false;
    this._tagType = null;
    this._openTag = null;
    this._closeTag = null;
    this._currentCode = "";
    this._currentLang = null;
    this._currentId = null;

    return { textParts: this._textParts, codeBlocks: this._codeBlocks, remaining };
  }

  reset() {
    this._buffer = "";
    this._inTag = false;
    this._tagType = null;
    this._openTag = null;
    this._closeTag = null;
    this._currentCode = "";
    this._currentLang = null;
    this._currentId = null;
    this._textAccum = "";
    this._textParts = [];
    this._codeBlocks = [];
  }
}



