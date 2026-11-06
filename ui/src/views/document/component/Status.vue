<template>
  <el-popover
    v-model:visible="visible"
    placement="top"
    trigger="hover"
    :popper-style="{ width: 'auto' }"
    :persistent="false"
  >
    <template #default>
      <span v-if="parseLabel">{{ parseLabel }}</span>
      <StatusTable
        v-if="visible && (!parseLabel || parseStatus === 'fallback')"
        :status="status"
        :statusMeta="statusMeta"
        :taskTypeMap="taskTypeMap"
        :stateMap="stateMap"
      ></StatusTable>
    </template>
    <template #reference>
      <el-text v-if="parseLabel" class="color-text-primary">
        <el-icon
          v-if="parseStatus === 'pending' || parseStatus === 'running'"
          class="is-loading color-primary"
        >
          <Loading />
        </el-icon>
        <el-icon v-else-if="parseStatus === 'failed'" class="color-danger"><CircleCloseFilled /></el-icon>
        <el-icon v-else class="color-warning"><SuccessFilled /></el-icon>
        {{ parseLabel }}
      </el-text>
      <el-text
        class="color-text-primary"
        v-else-if="aggStatus?.value === State.SUCCESS || aggStatus?.value === State.REVOKED"
      >
        <el-icon class="color-success"><SuccessFilled /></el-icon>
        {{ stateMap[aggStatus.value](aggStatus.key) }}
      </el-text>
      <el-text class="color-text-primary" v-else-if="aggStatus?.value === State.FAILURE">
        <el-icon class="color-danger"><CircleCloseFilled /></el-icon>
        {{ stateMap[aggStatus.value](aggStatus.key) }}
      </el-text>
      <el-text class="color-text-primary" v-else-if="aggStatus?.value === State.STARTED">
        <el-icon class="is-loading color-primary"><Loading /></el-icon>
        {{ stateMap[aggStatus.value](aggStatus.key) }}
      </el-text>
      <el-text class="color-text-primary" v-else-if="aggStatus?.value === State.PENDING">
        <el-icon class="is-loading color-primary"><Loading /></el-icon>
        {{ stateMap[aggStatus.value](aggStatus.key) }}
      </el-text>
      <el-text class="color-text-primary" v-else-if="aggStatus?.value === State.REVOKE">
        <el-icon class="is-loading color-primary"><Loading /></el-icon>
        {{ stateMap[aggStatus.value](aggStatus.key) }}
      </el-text>
    </template>
  </el-popover>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { TaskType, State } from '@/utils/status'
import StatusTable from '@/views/document/component/StatusTable.vue'
import { t } from '@/locales'
const props = defineProps<{ status: string; statusMeta: any; parseStatus?: string | null }>()
const parseLabel = computed(() => {
  const labels: Record<string, string> = {
    pending: t('views.document.fileStatus.PARSING'),
    running: t('views.document.fileStatus.PARSING'),
    fallback: t('views.document.fileStatus.PARSE_FALLBACK'),
    failed: t('views.document.fileStatus.PARSE_FAILED'),
  }
  return labels[props.parseStatus ?? ''] ?? ''
})
const visible = ref<boolean>(false)
const checkList: Array<string> = [
  State.REVOKE,
  State.STARTED,
  State.PENDING,
  State.FAILURE,
  State.REVOKED,
  State.SUCCESS,
]
const aggStatus = computed(() => {
  let obj = { key: 0, value: '' }
  for (const i in checkList) {
    const state = checkList[i]
    const index = props.status.indexOf(state)
    if (index > -1) {
      obj = { key: props.status.length - index, value: state }
      break
    }
  }
  return obj
})
const startedMap = {
  [TaskType.EMBEDDING]: t('views.document.fileStatus.EMBEDDING'),
  [TaskType.GENERATE_PROBLEM]: t('views.document.fileStatus.GENERATE'),
  [TaskType.SYNC]: t('views.document.fileStatus.SYNC'),
  [TaskType.TOKENIZE]: t('views.document.fileStatus.TOKENIZE'),
}
const taskTypeMap = {
  [TaskType.EMBEDDING]: t('views.knowledge.setting.vectorization'),
  [TaskType.GENERATE_PROBLEM]: t('views.document.generateQuestion.title'),
  [TaskType.SYNC]: t('views.knowledge.setting.sync'),
  [TaskType.TOKENIZE]: t('views.knowledge.setting.tokenize'),
}
const stateMap: any = {
  [State.PENDING]: (type: number) => t('views.document.fileStatus.PENDING'),
  [State.STARTED]: (type: number) => startedMap[type],
  [State.REVOKE]: (type: number) => t('common.status.REVOKE'),
  [State.REVOKED]: (type: number) => t('common.status.success'),
  [State.FAILURE]: (type: number) => t('common.status.fail'),
  [State.SUCCESS]: (type: number) => t('common.status.success'),
}
</script>
<style lang="scss" scoped></style>
