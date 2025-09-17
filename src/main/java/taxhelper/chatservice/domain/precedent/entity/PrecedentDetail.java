package taxhelper.chatservice.domain.precedent.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Table(name = "precedentDetail")
@Getter
@AllArgsConstructor
@NoArgsConstructor
public class PrecedentDetail {

    @Column
    @Id
    private String precedentNo; //판례번호

    @Column
    private String judgment; //판시사항

    @Column
    private String referencePrecedent; //참조 판례

    @Column
    private String typeOfIncident; // 사건 종류

    @Column
    private String summaryOfJudgment; // 판결 요지

    @Column
    private String sentencingDate; // 선고일자

    @Column
    private String courtName; // 법원명

    @Column
    private String  caseName; //사건명

    @Column
    private String detailsOfPrecedent; // 판례내용

    @Column
    private String caseNo; //사건번호

    @Column
    private String caseCode; // 사건 종류 코드


    @Column
    private String sentencing; //선고

    @Column
    private String judgmentType; // 판결유형

    @Column
    private String courtCode; // 법원코드

}
