const nextFrame = (source: Document) =>
  new Promise<void>(resolve => {
    const view = source.defaultView;
    if (view?.requestAnimationFrame) {
      view.requestAnimationFrame(() => resolve());
      return;
    }
    window.setTimeout(resolve, 0);
  });

export const waitForReportPaint = async (source: Document) => {
  const fonts = (source as Document & { fonts?: FontFaceSet }).fonts;
  try {
    await fonts?.ready;
  } catch {
    // Font readiness must not prevent a user from exporting the report.
  }
  // Native DB-GPT charts are mounted by React after the srcdoc iframe loads.
  // Two frames let the portal commit and its canvas paint before snapshotting.
  await nextFrame(source);
  await nextFrame(source);
};

const replaceCanvasesWithImages = (snapshot: HTMLHtmlElement, source: Document) => {
  const sourceCanvases = Array.from(source.querySelectorAll('canvas'));
  const snapshotCanvases = Array.from(snapshot.querySelectorAll('canvas'));
  snapshotCanvases.forEach((canvas, index) => {
    const sourceCanvas = sourceCanvases[index];
    if (!sourceCanvas) return;
    try {
      const image = snapshot.ownerDocument.createElement('img');
      image.src = sourceCanvas.toDataURL('image/png');
      image.width = sourceCanvas.width;
      image.height = sourceCanvas.height;
      image.alt = sourceCanvas.getAttribute('aria-label') || '分析图表';
      image.style.cssText =
        'display:block;max-width:100%;width:' +
        (sourceCanvas.clientWidth || sourceCanvas.width) +
        'px;height:' +
        (sourceCanvas.clientHeight || sourceCanvas.height) +
        'px;';
      canvas.replaceWith(image);
    } catch {
      // A tainted third-party canvas cannot be serialized. Keep the original
      // canvas in the snapshot so the browser still gets its best print attempt.
    }
  });
};

/**
 * Print a stable snapshot of the report iframe.
 *
 * Browser print can race with React portals mounted into the report iframe.
 * Snapshotting the painted iframe and serializing canvas charts as images makes
 * the PDF output match what the user sees, without changing the live report.
 */
export const printRenderedReport = async (iframe: HTMLIFrameElement): Promise<boolean> => {
  const printWindow = window.open('', '_blank');
  const source = iframe.contentDocument;
  if (!printWindow || !source?.documentElement) {
    printWindow?.close();
    return false;
  }

  try {
    await waitForReportPaint(source);
    const snapshot = source.documentElement.cloneNode(true) as HTMLHtmlElement;
    snapshot.querySelectorAll('script').forEach(script => script.remove());
    replaceCanvasesWithImages(snapshot, source);
    printWindow.document.open();
    printWindow.document.write('<!DOCTYPE html>' + snapshot.outerHTML);
    printWindow.document.close();
    await new Promise(resolve => window.setTimeout(resolve, 80));
    printWindow.focus();
    printWindow.print();
    printWindow.addEventListener('afterprint', () => printWindow.close(), { once: true });
    return true;
  } catch {
    printWindow.close();
    return false;
  }
};
