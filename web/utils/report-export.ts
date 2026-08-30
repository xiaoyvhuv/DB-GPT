export type ReportExportFormat = 'pdf' | 'png';

const reportFileName = (title: string, format: ReportExportFormat) => `${title.replace(/\s+/g, '_')}_report.${format}`;

/** Export a rendered report using DB-GPT's existing canvas-based export path. */
export const exportReportElement = async (element: HTMLElement, title: string, format: ReportExportFormat) => {
  const html2canvas = (await import('html2canvas')).default;
  const canvas = await html2canvas(element, {
    scale: 2,
    useCORS: true,
    allowTaint: true,
    backgroundColor: '#ffffff',
  });

  if (format === 'png') {
    const link = document.createElement('a');
    link.download = reportFileName(title, format);
    link.href = canvas.toDataURL('image/png');
    link.click();
    return;
  }

  const { jsPDF } = await import('jspdf');
  const pdf = new jsPDF({
    orientation: canvas.width > canvas.height ? 'landscape' : 'portrait',
    unit: 'px',
    format: [canvas.width, canvas.height],
  });
  pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 0, 0, canvas.width, canvas.height);
  pdf.save(reportFileName(title, format));
};
