/** Nama jenis ukuran yang dipahami teknisi, bukan kode di basis data. */
const LABELS: Record<string, string> = {
  rpm: "Putaran mesin",
  temperature: "Suhu",
  pressure: "Tekanan",
  fuel_level: "Level bahan bakar",
  fuel_flow: "Aliran bahan bakar",
  engine_load: "Beban mesin",
  voltage: "Tegangan",
  position: "Posisi GPS",
  raw: "Data mentah panel",
};

export function metricLabel(metric: string): string {
  const known = LABELS[metric];
  if (known) return known;
  const words = metric.replaceAll("_", " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
