export type CountForms = readonly [one: string, few: string, many: string];

export function formatCount(count: number, [one, few, many]: CountForms): string {
  const absolute = Math.abs(count);
  const lastTwo = absolute % 100;
  const last = absolute % 10;
  const form = lastTwo >= 11 && lastTwo <= 14
    ? many
    : last === 1
      ? one
      : last >= 2 && last <= 4
        ? few
        : many;
  return `${count} ${form}`;
}
