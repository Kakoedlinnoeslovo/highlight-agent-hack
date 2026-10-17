export function cityName(value) {
  const label = typeof value === 'object' && value ? value.city || value.label || '' : value;
  const normalized = String(label || 'San Francisco').trim().toLowerCase().replaceAll('_', ' ');
  if (['sf', 'san francisco'].includes(normalized)) return 'San Francisco';
  if (['ny', 'nyc', 'new york', 'new york city'].includes(normalized)) return 'New York';
  return String(label || 'San Francisco').trim();
}
export const cornerCity = corner => cityName(corner.city || corner.location);
export const cityCorners = (corners, city) => (corners || []).filter(corner => cornerCity(corner) === cityName(city));
export function availableCities(corners) {
  return [...new Set(['San Francisco', ...(corners || []).map(cornerCity)])].sort((a, b) => a === 'San Francisco' ? -1 : b === 'San Francisco' ? 1 : a.localeCompare(b));
}
