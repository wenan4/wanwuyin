import type { SealDef } from "./seal-geo";

export const elephantMaterials = [
  { name: "elephant_bronze", label: "青铜" },
  { name: "elephant_basalt", label: "玄武岩" },
  { name: "elephant_wood", label: "木雕" },
];

export function materialName(seal: SealDef): string | undefined {
  return elephantMaterials.find(material => material.name === seal.name)?.label;
}

export function displayName(seal: SealDef): string {
  if (seal.name === "yongzhong_mv") return seal.label + " · 四视图重建";
  const material = materialName(seal);
  return material ? seal.label + " · " + material : seal.label;
}

export function materialVariants(elephant: SealDef): SealDef[] {
  return elephantMaterials.map(({ name }) => ({
    ...elephant, name, glb: `objects/${name}/model.glb`,
  }));
}
