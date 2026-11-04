import type {Trip} from './domain'
import type {RoadRoute} from './routes'
export interface PlanMetrics {walkingKm:number|null;travelMinutes:number;visitMinutes:number;freeMinutes:number;days:{day:number;walkingKm:number|null;travelMinutes:number;visitMinutes:number;freeMinutes:number;finish:number;overMinutes:number;waitingMinutes:number}[]}
export interface PlanIssue {message:string;severity?:'error'|'warning';code?:string;options?:{action:string;activityId?:string;minutes?:number;day?:number}[]}
export interface PlanOption {id:string;label:string;trip:Trip;routes:Record<string,RoadRoute>;issues:PlanIssue[];metrics:PlanMetrics;includedIds:string[];omittedIds:string[]}
export interface PlanComparison {options:PlanOption[];diagnostic:Omit<PlanOption,'id'|'label'>|null;source:string;unavailable:{id:string;reason:string}[];metricNote:string;warnings:string[];search:{evaluatedStates:number;dayRouteQueries:number;maxDayRouteQueries:number;limited:boolean;reason:string|null;routeFailures:string[]}}
